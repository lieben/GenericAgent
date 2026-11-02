import re, subprocess

with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

m = re.search(r'<script>(.*?)</script>', text, re.DOTALL)
js_content = m.group(1)

# jsc (JavaScriptCore command-line tool on macOS)
mock_dom = """
var document = {
  getElementById: function(id) { 
    return { 
      value: '', 
      style: {}, 
      classList: { add: function(){}, remove: function(){} },
      textContent: '',
      innerHTML: '',
      focus: function(){}
    }; 
  },
  querySelectorAll: function() { return []; },
  createElement: function() { return { appendChild: function(){} }; }
};
var window = this;
var localStorage = {
  getItem: function(k) { return 'test'; },
  setItem: function(k, v) {}
};
var fetch = function() {
  return Promise.resolve({
    ok: true,
    json: function() { return Promise.resolve({}); }
  });
};
var alert = function(msg) {};
var confirm = function() { return true; };
"""

with open('temp/jsc_test.js', 'w', encoding='utf-8') as f:
    f.write(mock_dom + "\n" + js_content)

res = subprocess.run(['/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc', 'temp/jsc_test.js'], capture_output=True, text=True)
print("jsc returncode:", res.returncode)
print("jsc stdout:", res.stdout)
print("jsc stderr:", res.stderr)
