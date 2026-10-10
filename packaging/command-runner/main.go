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
	"strings"
)

func run() int {
	args := os.Args[1:]
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
