import re, subprocess

with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

m = re.search(r'<script>(.*?)</script>', text, re.DOTALL)
js_content = m.group(1)

wrapper = f"""
var window = this;
var document = {{
  getElementById: function(id) {{ return {{ value: '', style: {{}}, classList: {{ add: function(){{}}, remove: function(){{}} }} }}; }},
  querySelectorAll: function() {{ return []; }}
}};
var localStorage = {{
  getItem: function(k) {{ return 'test'; }},
  setItem: function(k, v) {{}}
}};
var fetch = function() {{ return Promise.resolve({{ json: function() {{ return {{}}; }} }}); }};

{js_content}
"""

with open('temp/syntax_test2.js', 'w', encoding='utf-8') as f:
    f.write(wrapper)

res = subprocess.run(['osascript', '-l', 'JavaScript', 'temp/syntax_test2.js'], capture_output=True, text=True)
print("Returncode:", res.returncode)
print("Stdout:", res.stdout)
print("Stderr:", res.stderr)
