"""
主程序 - 对话交互入口
支持对话记忆、会话管理、RAG 知识库检索、Agent Loop 自主检索
"""
from src.chatbot import Chatbot, AgentChatResult
from src.session import Session
from src.knowledge_base import KnowledgeBase
from config import AGENT_MODE_ENABLED


def print_help():
    """打印帮助信息"""
    print("\n" + "=" * 50)
    print("📋 对话命令:")
    print("  /help      - 显示帮助信息")
    print("  /clear     - 清空对话历史")
    print("  /history   - 显示对话历史")
    print("  /summary   - 显示会话摘要")
    print("  /save      - 保存当前会话")
    print("  /load      - 加载保存的会话")
    print("  /quit      - 退出程序")
    print("-" * 50)
    print("🤖 Agent 模式命令:")
    print("  /agent     - 切换 Agent 自主检索模式（开/关）")
    print("  /mode      - 查看当前对话模式")
    print("  /tool-log  - 查看工具调用历史（Agent 模式）")
    print("  /cost      - 查看 Token 消耗统计")
    print("-" * 50)
    print("📚 知识库命令:")
    print("  /add-docs   - 扫描并加载新文档到知识库")
    print("  /search     - 搜索知识库（支持元数据过滤）")
    print("    用法: /search <关键词> [--type md|txt|pdf|html] [--after 日期] [--before 日期]")
    print("    例: /search 机器学习 --type md --after 2026-01-01")
    print("  /kb-stats   - 显示知识库统计信息")
    print("  /rebuild    - 重建知识库索引")
    print("=" * 50)
    print("💡 提示: 直接输入消息进行对话，输入 /help 查看更多\n")


def handle_command(command: str, session: Session, chatbot: Chatbot, kb: KnowledgeBase):
    """处理特殊命令（以 / 开头）"""
    # 检查是否是命令（以 / 开头）
    if not command.startswith('/'):
        return False

    # 解析命令和参数（支持 /search <query> 格式）
    parts = command[1:].split(None, 1)
    cmd = parts[0].lower().strip()
    arg = parts[1].strip() if len(parts) > 1 else ""

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

    # ---- 知识库命令 ----
    elif cmd == 'add-docs':
        print("📂 正在扫描文档目录...")
        updated = kb.load_documents_from_dir()
        print(f"✓ 新增/更新文档: {updated} 个\n")

    elif cmd == 'search':
        if not arg:
            print("❌ 用法: /search <查询关键词> [--type markdown|text|pdf|html] [--after 日期] [--before 日期]\n")
        else:
            # 解析过滤参数（从 arg 中分离查询词和过滤器）
            import shlex
            try:
                tokens = shlex.split(arg)
            except ValueError:
                tokens = arg.split()

            query_parts = []
            filters = {}
            i = 0
            while i < len(tokens):
                if tokens[i] in ('--type', '-t') and i + 1 < len(tokens):
                    filters['doc_type'] = tokens[i + 1].lower()
                    i += 2
                elif tokens[i] in ('--after', '-a') and i + 1 < len(tokens):
                    filters['mtime_after'] = tokens[i + 1]
                    i += 2
                elif tokens[i] in ('--before', '-b') and i + 1 < len(tokens):
                    filters['mtime_before'] = tokens[i + 1]
                    i += 2
                else:
                    query_parts.append(tokens[i])
                    i += 1

            query = ' '.join(query_parts)
            if not query:
                print("❌ 请提供搜索关键词\n")
            else:
                filter_desc = ''
                if filters:
                    desc_parts = []
                    if 'doc_type' in filters:
                        desc_parts.append(f"类型={filters['doc_type']}")
                    if 'mtime_after' in filters:
                        desc_parts.append(f"晚于{filters['mtime_after']}")
                    if 'mtime_before' in filters:
                        desc_parts.append(f"早于{filters['mtime_before']}")
                    filter_desc = ' | 过滤: ' + ', '.join(desc_parts)

                print(f"\n🔍 混合检索: {query}{filter_desc}")
                print("-" * 50)
                results = kb.hybrid_search(query, top_k=3, filters=filters if filters else None)
                if not results:
                    print("（未找到相关结果）\n")
                else:
                    for i, r in enumerate(results, 1):
                        source_name = r['source'].replace('\\', '/').split('/')[-1]
                        bm25_s = r.get('bm25_score', 0)
                        vec_s = r.get('vector_score', 0)
                        doc_type = r.get('doc_type', '')
                        title = r.get('title', '')
                        print(f"  {i}. 📄 {source_name} [{doc_type}] {title}")
                        print(f"     块{r['chunk_index']} | 混合: {r['score']:.3f} | BM25: {bm25_s:.3f} | 向量: {vec_s:.3f}")
                        print(f"     {r['content'][:120]}...")
                        print()
                    print("-" * 50 + "\n")

    elif cmd == 'kb-stats':
        from config import CONTEXT_ENRICHMENT_ENABLED
        stats = kb.get_statistics()
        print("\n📊 知识库统计:")
        print("-" * 40)
        print(f"  存储后端:  {stats.get('store_type', 'N/A')}")
        print(f"  混合检索:  {'✓ 启用' if stats.get('hybrid_search') else '✗ 关闭'}")
        print(f"  上下文增强:{'✓ 启用' if CONTEXT_ENRICHMENT_ENABLED else '✗ 关闭'}")
        print(f"  文件数:    {stats.get('total_files', 'N/A')}")
        print(f"  块数:      {stats.get('total_chunks', 'N/A')}")
        print(f"  体积:      {stats.get('total_size_mb', 'N/A')} MB")
        print(f"  向量维度:  {stats.get('embeddings_dim', 'N/A')}")
        print(f"  缓存条目:  {stats.get('cache_size', 'N/A')}")
        print("-" * 40 + "\n")

    elif cmd == 'rebuild':
        print("🔄 正在重建知识库索引...")
        kb.rebuild_index()
        print("✓ 索引重建完成\n")

    # ---- Agent 模式命令 ----
    elif cmd == 'agent':
        chatbot.agent_mode = not chatbot.agent_mode
        mode_name = "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG"
        print(f"✓ 已切换至: {mode_name} 模式\n")

    elif cmd == 'mode':
        mode_name = "🤖 Agent 自主检索" if chatbot.agent_mode else "📎 一步式 RAG"
        agent_status = "启用" if AGENT_MODE_ENABLED else "禁用"
        print(f"\n  当前模式: {mode_name}")
        print(f"  默认模式: {'Agent' if AGENT_MODE_ENABLED else 'Simple'}")
        print(f"  输入 /agent 可切换模式\n")

    elif cmd == 'tool-log':
        log = session.get_tool_call_log()
        if not log:
            print("（无工具调用记录）\n")
        else:
            print("\n🔧 工具调用历史:")
            print("-" * 50)
            for i, entry in enumerate(log, 1):
                print(f"  {i}. {entry.get('tool_name', '?')}")
                args = entry.get('arguments', {})
                query = args.get('query', '?') if isinstance(args, dict) else str(args)
                print(f"     查询: {str(query)[:60]}")
                print(f"     耗时: {entry.get('duration_ms', '?')}ms")
                print(f"     时间: {entry.get('timestamp', '?')}")
                print()
            print("-" * 50 + "\n")

    elif cmd == 'cost':
        print("\n" + session.get_cost_summary() + "\n")

    elif cmd in ['quit', 'exit']:
        return "QUIT"

    else:
        print(f"❌ 未知命令: /{cmd}，输入 /help 查看所有命令\n")

    return True


def main():
    """主函数"""
    print("=" * 60)
    print("🤖 AI 知识库助手 (支持 Agent 自主检索)")
    print("=" * 60)
    print("💬 直接输入问题进行对话")
    print(f"🤖 当前模式: {'Agent 自主检索' if AGENT_MODE_ENABLED else '一步式 RAG'}")
    print("📌 输入 /help 查看所有命令")
    print("=" * 60)
    print()

    try:
        # 初始化知识库
        print("📂 初始化知识库...")
        kb = KnowledgeBase(embeddings_provider="local")
        print("✓ 知识库已初始化")

        # 预加载文档
        kb.load_documents_from_dir()

        # 初始化对话机器人和会话
        chatbot = Chatbot(knowledge_base=kb)
        session = Session()

        mode_str = "Agent 自主检索" if chatbot.agent_mode else "一步式 RAG"
        print(f"✓ 对话机器人初始化成功 ({mode_str})")
        print(f"✓ 新建会话: {session.session_id}\n")

        # 对话循环
        while True:
            try:
                user_input = input("你: ").strip()

                if not user_input:
                    continue

                # 处理特殊命令（以 / 开头）
                cmd_result = handle_command(user_input, session, chatbot, kb)

                if cmd_result == "QUIT":
                    save_prompt = input("是否保存本次对话? (y/n): ").lower().strip()
                    if save_prompt in ['y', 'yes']:
                        path = session.save()
                        print(f"✓ 会话已保存: {path}")
                    print("\n👋 再见！\n")
                    break

                elif cmd_result:
                    continue

                # ── 普通对话 ──
                if chatbot.agent_mode:
                    # Agent 自主循环检索模式
                    print(f"\n🤖 Agent 思考中（可自主检索）...\n")
                    result = chatbot.agent_chat(user_input, session.get_history())

                    # 打印工具调用摘要
                    if result.tool_call_log:
                        print(f"  📊 检索 {len(result.tool_call_log)} 次 | "
                              f"轮次 {result.rounds} | "
                              f"结束原因: {result.finish_reason}")

                    response = result.content

                    # 保存对话（含 tool 消息）
                    session.add_message("user", user_input)
                    session.add_message("assistant", response)
                    session.append_tool_call_log(result.tool_call_log)
                    if result.usage:
                        session.accumulate_usage(result.usage)

                    print(f"\nAgent: {response}\n")

                else:
                    # 一步式 RAG 模式（原有逻辑）
                    print("\n🔍 检索知识库 + 🤔 思考中...\n")
                    response = chatbot.chat_with_rag(user_input, session.get_history())

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
