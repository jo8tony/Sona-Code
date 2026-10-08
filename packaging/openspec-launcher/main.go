// A dependency-free native shim. Node and OpenSpec are adjacent application resources.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
)

func main() {
	executable, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(127)
	}
	root := filepath.Dir(filepath.Dir(executable))
	node := "node"
	if runtime.GOOS == "windows" {
		node += ".exe"
	}
	configRoot := os.Getenv("SONACODE_OPENSPEC_CONFIG_HOME")
	if configRoot == "" {
		base, err := os.UserConfigDir()
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(127)
		}
		configRoot = filepath.Join(base, "SonaCode", "openspec-runtime", "config")
	}
	config := filepath.Join(configRoot, "openspec", "config.json")
	if _, err := os.Stat(config); os.IsNotExist(err) {
		data, readErr := os.ReadFile(filepath.Join(root, "defaults", "openspec-config.json"))
		if readErr != nil {
			fmt.Fprintln(os.Stderr, readErr)
			os.Exit(127)
		}
		if err := os.MkdirAll(filepath.Dir(config), 0700); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(127)
		}
		// O_EXCL preserves a configuration created concurrently by another launcher.
		file, err := os.OpenFile(config, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err == nil {
			_, err = file.Write(data)
			closeErr := file.Close()
			if err == nil {
				err = closeErr
			}
		}
		if err != nil && !os.IsExist(err) {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(127)
		}
	}
	os.Setenv("XDG_CONFIG_HOME", configRoot)
	if dataRoot := os.Getenv("SONACODE_OPENSPEC_DATA_HOME"); dataRoot != "" {
		os.Setenv("XDG_DATA_HOME", dataRoot)
	}
	os.Setenv("OPENSPEC_TELEMETRY", "0")
	os.Setenv("OPENSPEC_NO_UPDATE_CHECK", "1")
	os.Setenv("DO_NOT_TRACK", "1")
	os.Unsetenv("NODE_OPTIONS")
	cli := filepath.Join(root, "package", "node_modules", "@fission-ai", "openspec", "bin", "openspec.js")
	policy := filepath.Join(root, "runtime", "offline.cjs")
	command := exec.Command(filepath.Join(root, "runtime", node), append([]string{"--require", policy, cli}, os.Args[1:]...)...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := command.Start(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(127)
	}
	interrupts := make(chan os.Signal, 1)
	signal.Notify(interrupts, os.Interrupt)
	go func() {
		for interrupt := range interrupts {
			_ = command.Process.Signal(interrupt)
		}
	}()
	err = command.Wait()
	signal.Stop(interrupts)
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			code := exit.ExitCode()
			if code < 0 {
				code = 130
			}
			os.Exit(code)
		}
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
