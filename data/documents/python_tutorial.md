# Python 基础教程

## 变量和数据类型

在 Python 中，不需要显式声明变量类型。Python 会根据赋予的值自动推断数据类型。

### 基本数据类型

1. **整数 (int)**: 表示整数值，如 42、-10、0
2. **浮点数 (float)**: 表示小数值，如 3.14、-2.5
3. **字符串 (str)**: 表示文本，用单引号或双引号括起来，如 "Hello"
4. **布尔值 (bool)**: 表示真或假，即 True 或 False

### 变量赋值

```python
# 整数赋值
age = 25
count = -10

# 浮点数赋值
height = 1.75
pi = 3.14159

# 字符串赋值
name = "Alice"
greeting = 'Hello World'

# 布尔值赋值
is_valid = True
is_active = False
```

## 控制流

### 条件语句

使用 `if`、`elif` 和 `else` 进行条件判断：

```python
score = 85
if score >= 90:
    print("优秀")
elif score >= 80:
    print("良好")
else:
    print("需要改进")
```

### 循环

使用 `for` 和 `while` 循环：

```python
# for 循环
for i in range(5):
    print(i)

# while 循环
count = 0
while count < 5:
    print(count)
    count += 1
```

## 函数定义

函数是可重复使用的代码块：

```python
def greet(name):
    """问候函数"""
    return f"Hello, {name}!"

result = greet("World")
print(result)  # 输出: Hello, World!
```
