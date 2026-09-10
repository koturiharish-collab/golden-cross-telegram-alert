name: NSE Golden Cross Scanner

"on":
  workflow_dispatch:
  schedule:
    - cron: "15 * * * *"

permissions:
  contents: write

jobs:
  scan:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install pandas requests yfinance

      - name: Run Golden Cross scanner
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python golden_cross.py

      - name: Save alert state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          if git diff --quiet; then
            echo "No state changes."
          else
            git add golden_cross_state.json
            git commit -m "Update Golden Cross alert state"
            git push
          fi
