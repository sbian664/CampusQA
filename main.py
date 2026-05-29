"""
主程序 - 对话交互入口
支持对话记忆和会话管理
"""
from src.chatbot import Chatbot
from src.session import Session


def print_help():
    """打印帮助信息"""
    print("\n" + "=" * 50)
    print("📋 命令列表 (在消息前加 / 使用):")
    print("  /help      - 显示帮助信息")
    print("  /clear     - 清空对话历史")
    print("  /history   - 显示对话历史")
    print("  /summary   - 显示会话摘要")
    print("  /save      - 保存当前会话")
    print("  /load      - 加载保存的会话")
    print("  /quit      - 退出程序")
    print("=" * 50)
    print("💡 提示: 输入普通消息直接对话，输入 /help 查看更多\n")


def handle_command(command: str, session: Session, chatbot: Chatbot):
    """处理特殊命令（以 / 开头）"""
    # 检查是否是命令（以 / 开头）
    if not command.startswith('/'):
        return False
    
    # 移除 / 并转换为小写
    cmd = command[1:].lower().strip()
    
    if cmd == 'help':
        print_help()
    
    elif cmd == 'clear':
        session.clear()
        print("✓ 对话历史已清空\n")
    
    elif cmd == 'save':
        path = session.save()
        print(f"✓ 会话已保存: {path}\n")
    
    elif cmd == 'load':
        if session.load():
            print(f"✓ 会话已加载: {session.session_id}")
            print(f"  消息数: {len(session.get_history())}\n")
        else:
            print(f"❌ 无法加载会话: {session.session_id}\n")
    
    elif cmd == 'history':
        history = session.get_history()
        if not history:
            print("（无对话历史）\n")
        else:
            print("\n📜 对话历史:")
            print("-" * 50)
            for i, msg in enumerate(history, 1):
                role = "👤 用户" if msg["role"] == "user" else "🤖 助手"
                print(f"{i}. {role}: {msg['content'][:70]}...")
            print("-" * 50 + "\n")
    
    elif cmd == 'summary':
        print(session.get_context_summary())
        print()
    
    elif cmd in ['quit', 'exit']:
        return "QUIT"
    
    else:
        print(f"❌ 未知命令: /{cmd}，输入 /help 查看所有命令\n")
    
    return True


def main():
    """主函数"""
    print("=" * 60)
    print("🤖 AI 对话助手 (带记忆功能)")
    print("=" * 60)
    print("💬 输入普通消息进行对话")
    print("📌 输入 /help 查看所有命令")
    print("=" * 60)
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
                
                # 处理特殊命令（以 / 开头）
                cmd_result = handle_command(user_input, session, chatbot)
                
                if cmd_result == "QUIT":
                    # 询问是否保存
                    save_prompt = input("是否保存本次对话? (y/n): ").lower().strip()
                    if save_prompt in ['y', 'yes']:
                        path = session.save()
                        print(f"✓ 会话已保存: {path}")
                    print("\n👋 再见！\n")
                    break
                
                elif cmd_result:
                    # 命令已处理
                    continue
                
                # 普通对话（非命令）
                print("\n🤔 Agent 思考中...\n")
                
                # 获取AI回复
                response = chatbot.chat_with_history(user_input, session.get_history())
                
                # 保存对话到会话
                session.add_message("user", user_input)
                session.add_message("assistant", response)
                
                print(f"Agent: {response}\n")
                
            except KeyboardInterrupt:
                print("\n\n⏸️  程序被中断")
                break
            except Exception as e:
                print(f"❌ 错误: {str(e)}\n")
    
    except Exception as e:
        print(f"❌ 初始化失败: {str(e)}")
        print("\n⚙️  请检查:")
        print("1. .env 文件是否正确配置")
        print("2. DEEPSEEK_API_KEY 是否设置")
        print("3. 网络连接是否正常")


if __name__ == "__main__":
    main()
