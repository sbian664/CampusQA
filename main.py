"""
主程序 - 对话交互入口
支持对话记忆和会话管理
"""
from src.chatbot import Chatbot
from src.session import Session


def print_help():
    """打印帮助信息"""
    print("\n" + "=" * 50)
    print("命令帮助:")
    print("  quit/exit/退出    - 退出程序")
    print("  clear/清空        - 清空对话历史")
    print("  save/保存         - 保存当前会话")
    print("  load/加载         - 加载保存的会话")
    print("  history/历史      - 显示对话历史")
    print("  summary/摘要      - 显示会话摘要")
    print("  help/帮助         - 显示此帮助")
    print("=" * 50 + "\n")


def handle_command(command: str, session: Session, chatbot: Chatbot):
    """处理特殊命令"""
    cmd = command.lower().strip()
    
    if cmd in ['help', '帮助']:
        print_help()
    
    elif cmd in ['clear', '清空']:
        session.clear()
        print("✓ 对话历史已清空\n")
    
    elif cmd in ['save', '保存']:
        path = session.save()
        print(f"✓ 会话已保存: {path}\n")
    
    elif cmd in ['load', '加载']:
        if session.load():
            print(f"✓ 会话已加载: {session.session_id}")
            print(f"  消息数: {len(session.get_history())}\n")
        else:
            print(f"❌ 无法加载会话: {session.session_id}\n")
    
    elif cmd in ['history', '历史']:
        history = session.get_history()
        if not history:
            print("（无对话历史）\n")
        else:
            print("\n对话历史:")
            print("-" * 40)
            for i, msg in enumerate(history, 1):
                role = "👤 用户" if msg["role"] == "user" else "🤖 助手"
                print(f"{i}. {role}: {msg['content'][:80]}...")
            print("-" * 40 + "\n")
    
    elif cmd in ['summary', '摘要']:
        print(session.get_context_summary())
        print()
    
    else:
        return False
    
    return True


def main():
    """主函数"""
    print("=" * 50)
    print("欢迎使用 AI 对话助手（带记忆功能）")
    print("=" * 50)
    print("提示: 输入 'help' 查看命令，'quit' 退出")
    print("=" * 50)
    print()
    
    try:
        # 初始化对话机器人和会话
        chatbot = Chatbot()
        session = Session()
        
        print("✓ 对话机器人初始化成功")
        print(f"✓ 新建会话: {session.session_id}\n")
        
        # 对话循环
        while True:
            try:
                user_input = input("你: ").strip()
                
                if not user_input:
                    continue
                
                # 检查特殊命令
                if user_input.lower() in ['quit', 'exit', '退出']:
                    # 询问是否保存
                    save_prompt = input("是否保存本次对话? (y/n): ").lower()
                    if save_prompt in ['y', 'yes', '是']:
                        path = session.save()
                        print(f"✓ 会话已保存: {path}")
                    print("\n再见！")
                    break
                
                # 处理其他特殊命令
                if handle_command(user_input, session, chatbot):
                    continue
                
                print("\nAgent 思考中...\n")
                
                # 获取AI回复
                response = chatbot.chat_with_history(user_input, session.get_history())
                
                # 保存对话到会话
                session.add_message("user", user_input)
                session.add_message("assistant", response)
                
                print(f"Agent: {response}\n")
                
            except KeyboardInterrupt:
                print("\n\n程序被中断")
                break
            except Exception as e:
                print(f"❌ 错误: {str(e)}\n")
    
    except Exception as e:
        print(f"❌ 初始化失败: {str(e)}")
        print("\n请检查:")
        print("1. .env 文件是否正确配置")
        print("2. DEEPSEEK_API_KEY 是否设置")
        print("3. 网络连接是否正常")


if __name__ == "__main__":
    main()
