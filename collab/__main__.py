"""直接运行包文件夹时（python3 <包路径>/collab ...），把包的上一级目录放进搜索路径。

这样包可以拷到任何项目里，从任意工作目录调用，不依赖当前所在目录。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collab.cli import main  # noqa: E402

sys.exit(main())
