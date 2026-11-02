import re

with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

m = re.search(r'<script>(.*?)</script>', text, re.DOTALL)
if not m:
    print("No script tag found!")
    exit(1)

js_content = m.group(1)
print(f"Extracted JS content, length: {len(js_content)}")

# Test via osascript syntax check
import subprocess
with open('temp/syntax_test.js', 'w', encoding='utf-8') as f:
    f.write(js_content)

res = subprocess.run(['osascript', '-l', 'JavaScript', '-e', f'eval($.NSString.stringWithContentsOfFileEncodingError("temp/syntax_test.js", 4, null).js)'], capture_output=True, text=True)
print("Returncode:", res.returncode)
print("Stdout:", res.stdout)
print("Stderr:", res.stderr)
