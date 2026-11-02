with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Let's extract preset logic
preset_block = """
  // Context Preset Management
  let currentPresetMode = 'dialogue';

  window.applyPreset = function(mode) {
    currentPresetMode = mode;
    const btnD = $('btnPresetDialogue');
    const btnN = $('btnPresetNovel');
    const btnC = $('btnPresetCustom');
    if (btnD) btnD.classList.remove('active');
    if (btnN) btnN.classList.remove('active');
    if (btnC) btnC.classList.remove('active');

    if (mode === 'dialogue') {
      if (btnD) btnD.classList.add('active');
      const d = $('ctxDialogueTurns');
      const s = $('ctxSummaryTurns');
      const m = $('ctxMacroInterval');
      if (d) d.value = 8;
      if (s) s.value = 5;
      if (m) m.value = 10;
      log('已切换至预设：💬 短小对话式 (对白8轮 / 梗概5轮 / 大纲10轮)');
    } else if (mode === 'novel') {
      if (btnN) btnN.classList.add('active');
      const d = $('ctxDialogueTurns');
      const s = $('ctxSummaryTurns');
      const m = $('ctxMacroInterval');
      if (d) d.value = 2;
      if (s) s.value = 3;
      if (m) m.value = 5;
      log('已切换至预设：📖 长篇小说式 (对白2轮 / 梗概3轮 / 大纲5轮)');
    } else {
      if (btnC) btnC.classList.add('active');
      log('已切换至预设：⚙️ 自定义参数');
    }
  };

  window.onCustomParamChange = function() {
    const d = parseInt($('ctxDialogueTurns').value, 10);
    const s = parseInt($('ctxSummaryTurns').value, 10);
    const m = parseInt($('ctxMacroInterval').value, 10);

    const btnD = $('btnPresetDialogue');
    const btnN = $('btnPresetNovel');
    const btnC = $('btnPresetCustom');
    if (btnD) btnD.classList.remove('active');
    if (btnN) btnN.classList.remove('active');
    if (btnC) btnC.classList.remove('active');

    if (d === 8 && s === 5 && m === 10) {
      currentPresetMode = 'dialogue';
      if (btnD) btnD.classList.add('active');
    } else if (d === 2 && s === 3 && m === 5) {
      currentPresetMode = 'novel';
      if (btnN) btnN.classList.add('active');
    } else {
      currentPresetMode = 'custom';
      if (btnC) btnC.classList.add('active');
    }
  };
"""

# Remove from bottom
# Find where Context Preset Management starts and remove until // Init
import re
text = re.sub(r'  // Context Preset Management.*?window\.onCustomParamChange = function\(\) \{.*?\n  \};\n', '', text, flags=re.DOTALL)

# Insert right after const $ = id => document.getElementById(id);
needle = '  const $ = id => document.getElementById(id);'
text = text.replace(needle, needle + '\n' + preset_block)

with open('plugins/roleplay/ui.html', 'w', encoding='utf-8') as f:
    f.write(text)

print('Updated ui.html with functions hoisted to the top of <script>')
