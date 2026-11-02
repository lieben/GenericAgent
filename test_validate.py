import re, subprocess

with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

script = re.search(r'<script>(.*?)</script>', text, re.DOTALL).group(1)

with open('temp/full_ui_script.js', 'w', encoding='utf-8') as f:
    f.write('var window = {}; var document = { getElementById: ()=>({}), querySelectorAll: ()=>[] };\n')
    f.write('var location = { origin: "http://localhost" };\n')
    f.write('var localStorage = { getItem: ()=>"sk-test", setItem: ()=>{} };\n')
    f.write('var fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) });\n')
    # replace const $ =
    s = script.replace('const $ =', 'var getEl =')
    s = re.sub(r'\$\(', 'getEl(', s)
    f.write(s)

res = subprocess.run(['osascript', '-l', 'JavaScript', 'temp/full_ui_script.js'], capture_output=True, text=True)
print('res code:', res.returncode)
print('res stderr:', res.stderr)
print('res stdout:', res.stdout)
