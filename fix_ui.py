import re

with open('plugins/roleplay/ui.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Check what functions are needed:
# 1. applyPreset(mode)
# 2. onCustomParamChange()
# 3. saving and loading context params in saveSession and selectSession and createNewDraft

preset_js = '''
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
      $('ctxDialogueTurns').value = 8;
      $('ctxSummaryTurns').value = 5;
      $('ctxMacroInterval').value = 10;
      log('已切换至预设：💬 短小对话式 (对白8轮 / 梗概5轮 / 大纲10轮)');
    } else if (mode === 'novel') {
      if (btnN) btnN.classList.add('active');
      $('ctxDialogueTurns').value = 2;
      $('ctxSummaryTurns').value = 3;
      $('ctxMacroInterval').value = 5;
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
'''

# 1. Insert preset_js before window.onload or initAuthCheck
init_idx = content.find('// Init')
if init_idx != -1:
    content = content[:init_idx] + preset_js + '\n' + content[init_idx:]
else:
    print('Warning: // Init not found')

# 2. In selectSession: load context parameters
# Look for: $('storyOpener').value = c.story || '';
target_select = "$('storyOpener').value = c.story || '';"
select_addon = """$('storyOpener').value = c.story || '';
      
      const ctx = c.context_config || {};
      const d = (ctx.dialogue_turns !== undefined) ? ctx.dialogue_turns : 8;
      const s = (ctx.summary_turns !== undefined) ? ctx.summary_turns : 5;
      const m = (ctx.macro_interval !== undefined) ? ctx.macro_interval : 10;
      $('ctxDialogueTurns').value = d;
      $('ctxSummaryTurns').value = s;
      $('ctxMacroInterval').value = m;
      if (d === 8 && s === 5 && m === 10) {
        applyPreset('dialogue');
      } else if (d === 2 && s === 3 && m === 5) {
        applyPreset('novel');
      } else {
        applyPreset('custom');
      }"""
content = content.replace(target_select, select_addon)

# 3. In createNewDraft: reset context parameters to default dialogue
target_draft = "$('storyOpener').value = '';"
draft_addon = """$('storyOpener').value = '';
    applyPreset('dialogue');"""
content = content.replace(target_draft, draft_addon)

# 4. In saveSession: include context_config
target_save = """variables: {
        char: $('charName').value.trim() || '沈星回',
        user: $('userName').value.trim() || '言言'
      }"""
save_addon = """variables: {
        char: $('charName').value.trim() || '沈星回',
        user: $('userName').value.trim() || '言言'
      },
      context_config: {
        mode: currentPresetMode,
        dialogue_turns: parseInt($('ctxDialogueTurns').value, 10) || 8,
        summary_turns: parseInt($('ctxSummaryTurns').value, 10) || 5,
        macro_interval: parseInt($('ctxMacroInterval').value, 10) || 10
      }"""
content = content.replace(target_save, save_addon)

with open('plugins/roleplay/ui.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Successfully updated plugins/roleplay/ui.html')
