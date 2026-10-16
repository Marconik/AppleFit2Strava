#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .conda/bin/python ]]; then
  print '未找到项目 Conda 环境，请先阅读 README.md。'
else
  .conda/bin/python health_to_strava.py
fi
read '?按回车关闭窗口…'
