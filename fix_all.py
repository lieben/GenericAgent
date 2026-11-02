with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Let's inspect the entire script block
import re
m = re.search(r'<script>(.*?)</script>', text, re.DOTALL)
if not m:
    print("Script not found")
    exit(1)

# Check all button onclicks in HTML
buttons = re.findall(r'<button[^>]*>', text)
for b in buttons:
    if 'Preset' in b:
        print('Preset btn:', b)

