# Salary Timer

Windows 桌面悬浮薪资计时器，使用 Python + PyQt6 实现。

当前版本：`1.0.0`

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

首次运行会在程序目录生成 `config.json`，用于保存月薪、工作日、工作时间段、窗口位置、折叠状态、透明度、显示模式、云端语录地址和开机自启设置。

## 打包为单 exe

```powershell
pyinstaller --noconfirm --onefile --windowed --name SalaryTimer main.py
```

打包结果在 `dist\SalaryTimer.exe`。
