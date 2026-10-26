# 星回 Ubuntu 安卓模拟器手机 SOP

## 已验证（2026-07-15）
- 方案：Android Emulator + KVM（无头）
- SDK：`/home/aimcafe/.local/android-sdk`
- AVD：`xinghui_phone`（Pixel 6 / Android 14 / google_apis x86_64）
- 串口：`emulator-5554`（启动后自动出现）
- 依赖：OpenJDK 17、用户在 `kvm` 组、`/dev/kvm` 可读写
- 启停助手：`xinghui-phone`（`~/.local/bin/xinghui-phone`）

## 启停
```bash
xinghui-phone start    # 无头启动
xinghui-phone wait     # 等到 boot_completed=1
xinghui-phone status
xinghui-phone stop
xinghui-phone adb devices -l
```

手动等价：
```bash
export ANDROID_HOME=/home/aimcafe/.local/android-sdk
export PATH="$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$PATH"
emulator -avd xinghui_phone -no-window -no-audio -no-boot-anim \
  -gpu swiftshader_indirect -accel on -memory 2048 -cores 2 &
adb wait-for-device
# 轮询 getprop sys.boot_completed == 1
```

## 操控
- adb：`/home/aimcafe/.local/bin/adb` 或 `$ANDROID_HOME/platform-tools/adb`
- UI dump：`from adb_ui import ui`（memory 在 PATH）
- 截图：`adb exec-out screencap -p > /tmp/screen.png`
- 点击/滑动：`adb shell input tap/swipe ...`（详见 mumu_adb_sop）

## 坑
- 无 KVM 权限时模拟器极慢或失败：需 `usermod -aG kvm $USER` 后重新登录；临时 `setfacl -m u:$USER:rw /dev/kvm`
- 首次需 `sdkmanager --licenses` + 装 `emulator`/`platform-tools`/`platforms;android-34`/`system-images;android-34;google_apis;x86_64`
- 与真机(vivo)可并存：真机走 `192.168.1.125:16384`，模拟器走 `emulator-5554`；多设备时 adb 命令加 `-s`
- 无窗口：日常自动化用 headless；若要看画面用 scrcpy 或临时去掉 `-no-window`
- 中文输入仍按 mumu_adb_sop（剪贴板/u2），别硬啃 `input text`
