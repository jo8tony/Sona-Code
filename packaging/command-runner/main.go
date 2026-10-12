// A shell-compatible bridge. All command processes belong to the backend.
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"time"
)

// ConPTY starts processes immediately. The gate lets Python assign this wrapper
// to its job before any user shell or startup script can create descendants.
func pty(args []string) int {
	gate := os.Getenv("SONACODE_PTY_GATE")
	if gate == "" || len(args) == 0 {
		return 2
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(gate); err == nil {
			break
		}
		if time.Now().After(deadline) {
			return 2
		}
		time.Sleep(10 * time.Millisecond)
	}
	os.Unsetenv("SONACODE_PTY_GATE")
	// The shell receives console Ctrl+C itself; keep the wrapper alive to reap it.
	interrupts := make(chan os.Signal, 1)
	signal.Notify(interrupts, os.Interrupt)
	defer signal.Stop(interrupts)
	child := exec.Command(args[0], args[1:]...)
	child.Stdin, child.Stdout, child.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		if exited, ok := err.(*exec.ExitError); ok {
			return exited.ExitCode()
		}
		fmt.Fprintln(os.Stderr, "SonaCode terminal launch failed:", err)
		return 1
	}
	return 0
}

func run() int {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "--sona-pty" {
		return pty(args[1:])
	}
	command := ""
	for index, arg := range args {
		if (arg == "-c" || strings.EqualFold(arg, "-Command") || strings.EqualFold(arg, "/c")) && index+1 < len(args) {
			command = args[index+1]
			break
		}
	}
	if command == "" {
		fmt.Fprintln(os.Stderr, "SonaCode runner requires -c and a command")
		return 2
	}
	cwd, err := os.Getwd()
	if err != nil {
		return 2
	}
	env := map[string]string{}
	for _, item := range os.Environ() {
		key, value, found := strings.Cut(item, "=")
		if found {
			env[key] = value
		}
	}
	body, _ := json.Marshal(map[string]any{"command": command, "args": args, "cwd": cwd, "session_id": env["SONACODE_COMMAND_SESSION"], "shell": env["SONACODE_COMMAND_SHELL"], "env": env})
	req, err := http.NewRequest("POST", env["SONACODE_COMMAND_URL"]+"/run", bytes.NewReader(body))
	if err != nil {
		return 2
	}
	req.Header.Set("Authorization", "Bearer "+env["SONACODE_COMMAND_TOKEN"])
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Transport: &http.Transport{Proxy: nil}}
	resp, err := client.Do(req)
	if err != nil {
		fmt.Fprintln(os.Stderr, "SonaCode command manager disconnected")
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		fmt.Fprintln(os.Stderr, "SonaCode command was rejected")
		return 1
	}
	decoder := json.NewDecoder(resp.Body)
	for {
		var frame struct {
			Data string `json:"data"`
			Exit *int   `json:"exit"`
		}
		if err := decoder.Decode(&frame); err != nil {
			if err != io.EOF {
				fmt.Fprintln(os.Stderr, "SonaCode command stream interrupted")
			}
			return 1
		}
		if frame.Data != "" {
			data, err := base64.StdEncoding.DecodeString(frame.Data)
			if err != nil {
				return 1
			}
			if _, err = os.Stdout.Write(data); err != nil {
				return 1
			}
		}
		if frame.Exit != nil {
			return *frame.Exit
		}
	}
}

func main() { os.Exit(run()) }
