"""
主程序 - 对话交互入口
"""
from src.chatbot import Chatbot


def main():
    """主函数"""
    print("=" * 50)
    print("欢迎使用 AI 对话助手")
    print("=" * 50)
    print("提示: 输入 'quit' 或 'exit' 退出")
    print("=" * 50)
    print()
    
    try:
        # 初始化对话机器人
        chatbot = Chatbot()
        print("✓ 对话机器人初始化成功")
        print()
        
        # 对话循环
        while True:
            try:
                user_input = input("你: ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() in ['quit', 'exit', '退出']:
                    print("\n再见！")
                    break
                
                print("\nAgent 思考中...\n")
                response = chatbot.chat(user_input)
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
