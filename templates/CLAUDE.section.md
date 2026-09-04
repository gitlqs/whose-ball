<!-- whose-ball:start -->
## Task Tracker（球在谁手里）—— 每个会话必须维护

本项目用 whose-ball tracker（`{TRACKER_DIR}/tracker.py` + tracker.db + index.html）作为所有进行中事项"球在谁手里"的唯一事实源。

**同步规则（强制）：** 满足以下任一情况时，Claude 必须在同一回合内用 CLI 把变化写进 tracker，不要先问，写完在回复末尾用一句话说明写了什么：

- 用户告知某项目有进展（对方回了、报价到了、文件到了……）
- Claude 替用户完成了动作（发了邮件、建了记录、算了数……）
- 出现了新事项/新项目，或项目完成/放弃

写入用 CLI（不依赖 server 在跑），在项目根目录执行：

```bash
{CLI} log 项目代号 "发生了什么，带具体信息"
```

常用命令：

| 场景 | 命令 |
|---|---|
| 新项目 | `{CLI} add "代号" -t "谁/什么 · 一句话" -c 类别 -s 阶段` |
| 记进展 | `{CLI} log "代号" "发生了什么"` |
| 球转手 | `{CLI} wait "代号" customer\|vendor\|internal\|me "等什么事" --who 人名 --clear` |
| 完成某等待项 | `{CLI} done <wait_id> "备注"`（wait_id 用 `show` 查） |
| 催过了 | `{CLI} nudge <wait_id>` |
| 改阶段 | `{CLI} update "代号" -s 阶段名` |
| 完成/放弃 | `{CLI} close "代号" "原因"` |
| 挂链接 | `{CLI} ref "代号" "标签" "URL或路径"` |
| 存资料 | `{CLI} note "代号" "标题" "内容"` 或 `-f 文件.md` |
| 查状态 | `{CLI} show "代号"` / `{CLI} list` |

要点：

- 代号匹配支持唯一子串，会话里提到项目时先 `show` 一眼再动手
- "球转手"用 `wait --clear`：了结旧等待项、立新等待项，一条命令完成
- 阶段常用值（保持一致便于统计）：{STAGES}
- 类别（-c）常用值：{CATEGORIES}
- 时间线事件要带信息量（写"X 报价 型号 $金额 条款"，别写"有更新"）
- 等待方四选一：customer=等对方/客户，vendor=等供应商/第三方，internal=等内部同事，me=轮到用户自己

Web 看板：`{CLI} serve` → http://127.0.0.1:8765（或用 Claude 的 preview，`.claude/launch.json` 已配好 `tracker`）。
<!-- whose-ball:end -->
