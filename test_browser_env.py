with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Let's inspect lines around btnD in ui.html
import re
m = re.search(r'window\.applyPreset = function\(mode\) \{(.*?)\};', text, re.DOTALL)
if m:
    print('applyPreset code:\n', m.group(1))

