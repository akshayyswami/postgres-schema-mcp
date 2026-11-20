# Records docs/demo.gif. Run from the repo root: vhs docs/demo.tape
# Offline: evals/demo.py reads the frozen catalog, so no database is needed.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1000
Set Height 470
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

Hide
Type "export PS1='$ '"
Enter
Type "clear"
Enter
Show

Type "uv run python evals/demo.py"
Sleep 600ms
Enter
Sleep 9s
