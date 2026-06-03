# Phase 10 B1 学习笔记

## subprocess.run vs Popen

`run()` 等进程结束才返回，异常时不杀子进程（泄漏）；`Popen()` 立即返回，持有 proc 引用可 kill/wait。

无 shell 模式：`shell=False` 直接 execve 启动程序，shell 语法（管道/重定向/$变量）全部失效，但命令注入手法不再危险。

## 为什么 agent 不需要 shell

Shell 是给人用的——人嫌麻烦，`cat x | grep y` 一行搞定。但 agent 没有"打字累"的问题，LLM 直接输出结构化工具调用参数，想过滤就再调一次工具，不需要管道。去掉 shell = 消除命令注入面 + 零功能损失。

## 为什么 shell=False 防注入

`shlex.split()` 只按空格/引号拆参数，不解释 `;` `|` `$()` 等 shell 语法，拆出来全当普通字符串传给程序。注入代码没有机会被当成命令执行。防 `rm -rf /` 靠 approval 层，shell=False 防的是参数注入。
