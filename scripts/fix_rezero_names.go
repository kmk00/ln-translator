package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Użycie: go run main.go <nazwa_pliku.txt>")
		return
	}

	inputFile := os.Args[1]
	outputFile := strings.TrimSuffix(inputFile, ".txt") + "_poprawione.txt"

	content, err := os.ReadFile(inputFile)
	if err != nil {
		fmt.Printf("Błąd: nie można odczytać pliku: %v\n", err)
		return
	}

	text := string(content)

	reIncidental := regexp.MustCompile(`\[([^:\n\[\]]+)\]`)
	text = reIncidental.ReplaceAllString(text, `<$1>`)

	reDialogue := regexp.MustCompile(`(?ms)^\[([^:\[\]\n]+):\s*(.*?)\]`)
	text = reDialogue.ReplaceAllString(text, `$1: [$2]`)

	err = os.WriteFile(outputFile, []byte(text), 0644)
	if err != nil {
		fmt.Printf("Błąd: nie można zapisać pliku: %v\n", err)
		return
	}

	fmt.Printf("Sukces! Przetworzono plik: %s -> %s\n", inputFile, outputFile)
}