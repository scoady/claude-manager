package ui

import (
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/lipgloss/table"
)

// RenderTable creates a styled table with the given headers and rows.
func RenderTable(headers []string, rows [][]string) string {
	t := table.New().
		Border(lipgloss.NormalBorder()).
		BorderStyle(lipgloss.NewStyle().Foreground(ColorBorder)).
		Headers(headers...).
		StyleFunc(func(row, col int) lipgloss.Style {
			if row == table.HeaderRow {
				return lipgloss.NewStyle().
					Bold(true).
					Foreground(ColorPurple).
					Padding(0, 1)
			}
			return lipgloss.NewStyle().
				Padding(0, 1).
				Foreground(ColorWhite)
		})

	for _, row := range rows {
		t.Row(row...)
	}

	return t.Render()
}
