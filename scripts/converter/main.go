package main

import (
	"bytes"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/ledongthuc/pdf"
)

func main() {
	// Definicja flagi --split
	splitPtr := flag.Bool("split", false, "Podziel tekst na osobne pliki dla każdego rozdziału")
	
	// Parsowanie argumentów
	flag.Parse()
	
	// flag.Args() zwraca argumenty bez flag (czyli nazwy plików)
	args := flag.Args()

	if len(args) < 2 {
		fmt.Println("Użycie: go run main.go [--split] <plik_wejsciowy.pdf> <plik_wyjsciowy.txt>")
		os.Exit(1)
	}

	inputPath := args[0]
	outputPath := args[1]

	fmt.Printf("Pobieranie tekstu z %s...\n", inputPath)
	rawText, err := extractTextFromPDF(inputPath)
	if err != nil {
		fmt.Printf("Błąd odczytu PDF: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("Formatowanie tekstu...")
	formattedText := formatSentences(rawText)

	// Obsługa podziału na rozdziały
	if *splitPtr {
		fmt.Println("Wykryto flagę --split. Dzielenie na rozdziały...")
		err = splitIntoChapters(formattedText, outputPath)
		if err != nil {
			fmt.Printf("Błąd podczas dzielenia na rozdziały: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("Sukces! Rozdziały zapisano w folderze.\n")
	} else {
		err = os.WriteFile(outputPath, []byte(formattedText), 0644)
		if err != nil {
			fmt.Printf("Błąd zapisu do pliku: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("Sukces! Sformatowany tekst zapisano do %s\n", outputPath)
	}
}

func extractTextFromPDF(path string) (string, error) {
	f, r, err := pdf.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	b, err := r.GetPlainText()
	if err != nil {
		return "", err
	}

	var buf bytes.Buffer
	buf.ReadFrom(b)

	return buf.String(), nil
}

func formatSentences(text string) string {
	text = strings.ReplaceAll(text, "\n", " ")
	text = strings.ReplaceAll(text, "\r", " ")

	spaceRegex := regexp.MustCompile(`\s+`)
	text = spaceRegex.ReplaceAllString(text, " ")

	chapterRegex := regexp.MustCompile(`(CHAPTER\s+[0-9IVXLCDM]+\s*:\s*[A-Z\s]+?[A-Z])\s+([A-Z][a-z]|[“"”'‘’][A-Z]|[A-Z]\s+[a-z])`)
	text = chapterRegex.ReplaceAllString(text, "$1.\n$2")

	abbreviations := []string{
		"Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Inc.",
		"np.", "tzw.", "itp.", "itd.", "m.in.", "lek.", "ul.", "al.",
	}
	for _, abbr := range abbreviations {
		safeAbbr := strings.ReplaceAll(abbr, ".", "@@")
		text = strings.ReplaceAll(text, abbr, safeAbbr)

		titleAbbr := strings.Title(abbr)
		if titleAbbr != abbr {
			safeTitleAbbr := strings.ReplaceAll(titleAbbr, ".", "@@")
			text = strings.ReplaceAll(text, titleAbbr, safeTitleAbbr)
		}
	}

	sentenceRegex := regexp.MustCompile(`([.!?][”"’']?)\s+`)
	text = sentenceRegex.ReplaceAllString(text, "$1\n")

	forceChapterNewline := regexp.MustCompile(`([^\n])\s*(CHAPTER\s+[0-9IVXLCDM]+\s*:)`)
	text = forceChapterNewline.ReplaceAllString(text, "$1\n$2")

	text = strings.ReplaceAll(text, "@@", ".")

	lines := strings.Split(text, "\n")
	lines = removeFootnotes(lines)

	return strings.TrimSpace(strings.Join(lines, "\n"))
}

func removeFootnotes(lines []string) []string {
	var result []string
	i := 0

	for i < len(lines) {
		line := strings.TrimSpace(lines[i])

		if line == "1." {
			isFootnote := false
			targetIdx := -1

			for j := i + 1; j < len(lines); j++ {
				lookahead := strings.TrimSpace(lines[j])
				if strings.HasPrefix(lookahead, "CHAPTER ") {
					isFootnote = true
					targetIdx = j
					break
				}
				if j-i > 35 {
					break
				}
				if j == len(lines)-1 {
					isFootnote = true
					targetIdx = len(lines)
					break
				}
			}

			if isFootnote {
				i = targetIdx
				continue
			}
		}

		result = append(result, lines[i])
		i++
	}

	return result
}

// splitIntoChapters zajmuje się tworzeniem folderu i plików dla każdego rozdziału
func splitIntoChapters(text string, outputPath string) error {
	// Usuwamy .txt z nazwy, aby utworzyć ładną nazwę folderu
	dirName := strings.TrimSuffix(outputPath, ".txt")
	
	// Tworzymy folder
	err := os.MkdirAll(dirName, 0755)
	if err != nil {
		return err
	}

	lines := strings.Split(text, "\n")
	currentChapterName := "00_Prologue"
	var currentContent []string

	for _, line := range lines {
		// Jeśli linia zaczyna się od CHAPTER, to znaczy, że zaczynamy nowy plik
		if strings.HasPrefix(line, "CHAPTER ") {
			// Zapisujemy poprzedni rozdział (jeśli ma jakąś treść)
			if len(currentContent) > 0 {
				saveChapterFile(dirName, currentChapterName, currentContent)
			}
			
			// Tworzymy bezpieczną nazwę pliku z tytułu rozdziału (np. usuwamy kropki i dwukropki)
			currentChapterName = sanitizeFilename(line)
			// Rozpoczynamy nową zawartość od tytułu
			currentContent = []string{line}
		} else {
			// Zwykła linia tekstu - dodajemy do obecnego rozdziału
			currentContent = append(currentContent, line)
		}
	}

	// Nie zapominamy zapisać ostatniego rozdziału po wyjściu z pętli!
	if len(currentContent) > 0 {
		saveChapterFile(dirName, currentChapterName, currentContent)
	}

	return nil
}

// sanitizeFilename czyści nazwę rozdziału z niedozwolonych znaków
func sanitizeFilename(name string) string {
	// Usuwamy kropkę z końca, jeśli jest (np. "CHAPTER 3: MELISSA.")
	name = strings.TrimSuffix(name, ".")
	
	// Zamieniamy dwukropek na myślnik, aby Windows nie krzyczał o błąd
	name = strings.ReplaceAll(name, ":", " -")
	
	// Usuwamy potencjalne znaki, które psują nazwy plików w systemach operacyjnych
	invalidChars := []string{"\\", "/", "*", "?", "\"", "<", ">", "|"}
	for _, ch := range invalidChars {
		name = strings.ReplaceAll(name, ch, "")
	}
	
	return strings.TrimSpace(name)
}

// saveChapterFile to funkcja pomocnicza zapisująca konkretny plik
func saveChapterFile(dir, name string, lines []string) error {
	filePath := filepath.Join(dir, name+".txt")
	content := strings.Join(lines, "\n")
	return os.WriteFile(filePath, []byte(content), 0644)
}