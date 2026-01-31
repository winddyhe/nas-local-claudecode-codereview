' 无窗口启动 InternalCodeReviewServer（运行 start_server.bat，窗口隐藏）
' 双击或由计划任务调用均可
Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
ScriptDir = Fso.GetParentFolderName(WScript.ScriptFullName)
BatPath = Fso.BuildPath(ScriptDir, "start_server.bat")
' 0 = 隐藏窗口, False = 不等待结束
WshShell.Run "cmd /c """ & BatPath & """", 0, False
