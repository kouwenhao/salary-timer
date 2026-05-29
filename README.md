# Salary Timer

Windows 桌面悬浮薪资计时器，使用 Python + PyQt6 实现。

当前版本：`1.0.3`

云端语录默认读取：

```text
https://raw.githubusercontent.com/kouwenhao/salary-timer/main/quotes.json
```

更新信息默认读取：

```text
https://raw.githubusercontent.com/kouwenhao/salary-timer/main/update.json
```

## 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```

首次运行会在用户配置目录生成 `config.json`，用于保存月薪、工作日、工作时间段、窗口位置、折叠状态、透明度、显示模式、云端语录地址和开机自启设置。Windows 默认位置：

```text
%APPDATA%\SalaryTimer\config.json
```

## 打包为单 exe

```powershell
pyinstaller --noconfirm --onefile --windowed --name SalaryTimer --distpath dist_installer main.py
```

打包结果在 `dist_installer\SalaryTimer.exe`。

## 生成安装包

安装 Inno Setup 后执行：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

安装包输出在 `installer\SalaryTimerSetup.exe`。安装后程序固定放在：

```text
%LOCALAPPDATA%\Programs\SalaryTimer
```

安装包会自动创建桌面快捷方式。程序配置仍保存在 `%APPDATA%\SalaryTimer\config.json`，更新安装不会覆盖用户配置。
