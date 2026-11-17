# 1Password secret references — safe to commit: these are pointers, not secrets.
# Format: NAME="op://<vault>/<item>/<field>"   (your vault is "Personal")
# Run anything that needs them with:   op run --env-file=".env.tpl" -- <command>
# op injects the real values into that process only; they never touch disk or the terminal.
#
# Examples (replace the item names with yours; add a line per secret):
# OPENAI_API_KEY="op://Personal/OpenAI/credential"
# GITHUB_TOKEN="op://Personal/GitHub Token/credential"
# EXAMPLE_API_KEY="op://Personal/ItemName/credential"
