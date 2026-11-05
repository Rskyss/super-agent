"""调度员接口：每家一个适配器，对上都吐出同一种“聊天事件”，聊天页面不用管背后是谁。

聊天事件（字典，都有 type）：
  opening      超级助手自动交代（灰色提示）            text
  user         你说的话                                 text
  delta        调度员回复的一小段（边写边出）           item, text
  message      调度员的一条完整回复                     item, text
  activity     调度员在干活：读文件、运行命令、改文件…  item, kind, label, status(started/done/failed/declined), detail
  image        调度员画的图                             item, path, prompt
  approval     请你允许                                 id, kind(command/file/permissions), command, reason
  approval_done 你的回答                                id, decision
  turn_started / turn_done  一轮开始 / 结束             status(completed/interrupted/failed), error
  usage        用量                                     total
  notice / error / status   提示 / 出错 / 连接状态      text
另带 history=True 的，是打开时取回的旧对话。
"""
