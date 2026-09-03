# -*- coding: utf-8 -*-
"""临时调试：reasoning 切分位置 15"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat2api as c

sample = ('先看结构。<tool_calls><invoke name="Read" tool="fs">'
          '<parameter name="f">a.py</parameter></invoke></tool_calls>再决定。')
expected = "先看结构。再决定。"

f = c.ReasoningXMLFilter()
a = f.feed(sample[:15])
b = f.feed(sample[15:])
fl = f.flush()
print('p15 a=%r' % a)
print('p15 b=%r' % b)
print('p15 flush=%r' % fl)
print('p15 join=%r  expected=%r  eq=%s' % (a + b + fl, expected, a + b + fl == expected))
print('sample[:15]=%r' % sample[:15])
print('sample[15:20]=%r' % sample[15:20])
