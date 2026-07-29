#!/usr/bin/env python3
"""Build 薪尽火传 Geometry Board assets."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "boards"
WIDTH = 1200
HEIGHT = 675
FONT = "PingFang SC, Source Han Sans SC, Inter, Arial, sans-serif"


def scene(
    board_id: str,
    title: str,
    subtitle: str,
    core_message: str,
    composition: str,
    focus_node: str,
    nodes: list[dict],
    edges: list[dict],
) -> dict:
    return {
        "canvas": {
            "width": WIDTH,
            "height": HEIGHT,
            "ratio": "16:9",
            "background": "#FFFFFF",
            "grid": 8,
        },
        "intent": {
            "core_message": core_message,
            "composition": composition,
            "focus_node": focus_node,
        },
        "style": {
            "theme": "geometry-minimal",
            "accent_color": "#2F6BFF",
            "line_weight": 1,
            "corner_radius": 0,
        },
        "meta": {"id": board_id, "title": title, "subtitle": subtitle},
        "nodes": nodes,
        "edges": edges,
    }


def shell(title: str, subtitle: str, body: str, defs: str = "") -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <defs>
    <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#222222" stroke-width="1"/></marker>
    <marker id="arrow-blue" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#2F6BFF" stroke-width="1.5"/></marker>
    {defs}
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="#FFFFFF"/>
  <g font-family="{FONT}">
    <text x="88" y="66" fill="#111111" font-size="32" font-weight="600">{title}</text>
    <text x="88" y="98" fill="#666666" font-size="16">{subtitle}</text>
    <line x1="88" y1="126" x2="1112" y2="126" stroke="#E8E8E8"/>
    {body}
  </g>
</svg>
"""


def board_01() -> tuple[dict, str]:
    nodes = [
        {"id": "agent-a", "type": "circle", "label": "Agent A", "importance": 2},
        {"id": "agent-b", "type": "circle", "label": "Agent B", "importance": 2},
        {"id": "agent-c", "type": "circle", "label": "Agent C", "importance": 2},
        {"id": "fire", "type": "point", "label": "可继续工作的现场", "importance": 5},
    ]
    edges = [
        {"from": "agent-a", "to": "agent-b", "relation": "交棒"},
        {"from": "agent-b", "to": "agent-c", "relation": "再接力"},
    ]
    semantic = scene(
        "01-why",
        "Agent 有尽，火种不断",
        "会话会结束，工作现场必须继续",
        "单个 Agent 有边界，可靠的接力让项目跨会话延续",
        "tension-contrast",
        "fire",
        nodes,
        edges,
    )
    body = """
    <text x="160" y="210" fill="#999999" font-size="12" letter-spacing="2">有尽</text>
    <text x="790" y="210" fill="#2F6BFF" font-size="12" letter-spacing="2">不尽</text>
    <line x1="600" y1="190" x2="600" y2="548" stroke="#E8E8E8"/>
    <circle cx="214" cy="350" r="58" fill="#F5F5F5" stroke="#B8B8B8"/>
    <text x="214" y="344" text-anchor="middle" fill="#111111" font-size="18" font-weight="600">一次会话</text>
    <text x="214" y="371" text-anchor="middle" fill="#777777" font-size="12">额度 · 上下文 · 关闭</text>
    <path d="M300 350 C388 292 454 292 548 350" fill="none" stroke="#B8B8B8" stroke-dasharray="5 7"/>
    <line x1="508" y1="320" x2="548" y2="380" stroke="#B8B8B8"/>
    <line x1="548" y1="320" x2="508" y2="380" stroke="#B8B8B8"/>
    <text x="376" y="432" text-anchor="middle" fill="#999999" font-size="14">只靠聊天记忆，容易断线</text>

    <circle cx="724" cy="350" r="48" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="904" cy="350" r="48" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="1084" cy="350" r="48" fill="#FFFFFF" stroke="#222222"/>
    <text x="724" y="356" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">Agent A</text>
    <text x="904" y="356" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">Agent B</text>
    <text x="1084" y="356" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">Agent C</text>
    <path d="M772 350 C806 310 850 310 856 350" fill="none" stroke="#2F6BFF" stroke-width="1.5" marker-end="url(#arrow-blue)"/>
    <path d="M952 350 C986 310 1030 310 1036 350" fill="none" stroke="#2F6BFF" stroke-width="1.5" marker-end="url(#arrow-blue)"/>
    <circle cx="814" cy="319" r="6" fill="#2F6BFF"/>
    <circle cx="994" cy="319" r="6" fill="#2F6BFF"/>
    <text x="904" y="448" text-anchor="middle" fill="#2F6BFF" font-size="18" font-weight="600">状态 · 产物 · 标准 · 下一步</text>
    <text x="904" y="480" text-anchor="middle" fill="#777777" font-size="14">传下去的是可继续工作的现场</text>
    """
    return semantic, shell(semantic["meta"]["title"], semantic["meta"]["subtitle"], body)


def board_02() -> tuple[dict, str]:
    labels = ["交棒", "接棒", "续租", "完成", "验收"]
    ids = ["offer", "accept", "heartbeat", "complete", "verify"]
    nodes = [
        {"id": item_id, "type": "point", "label": label, "importance": 5 if item_id == "verify" else 3}
        for item_id, label in zip(ids, labels)
    ]
    edges = [
        {"from": ids[index], "to": ids[index + 1], "relation": "推进"}
        for index in range(len(ids) - 1)
    ]
    semantic = scene(
        "02-lifecycle",
        "一根棒，必须跑完整个闭环",
        "完成不是终点，验收才算交付",
        "可验证的接力必须经历交棒、接棒、执行、完成和验收",
        "axis-flow",
        "verify",
        nodes,
        edges,
    )
    xs = [160, 380, 600, 820, 1040]
    groups = []
    for index, (x, label) in enumerate(zip(xs, labels), 1):
        accent = index == 5
        groups.append(
            f"""
    <circle cx="{x}" cy="350" r="{40 if accent else 30}" fill="#FFFFFF" stroke="{'#2F6BFF' if accent else '#222222'}" stroke-width="{'1.5' if accent else '1'}"/>
    {'<circle cx="' + str(x) + '" cy="350" r="6" fill="#2F6BFF"/>' if accent else ''}
    <text x="{x}" y="292" text-anchor="middle" fill="#999999" font-size="12" letter-spacing="2">0{index}</text>
    <text x="{x}" y="424" text-anchor="middle" fill="{'#2F6BFF' if accent else '#111111'}" font-size="18" font-weight="600">{label}</text>
    """
        )
    body = f"""
    <line x1="160" y1="350" x2="1040" y2="350" stroke="#222222"/>
    {''.join(groups)}
    <text x="160" y="474" text-anchor="middle" fill="#777777" font-size="12">说清任务</text>
    <text x="380" y="474" text-anchor="middle" fill="#777777" font-size="12">核对输入</text>
    <text x="600" y="474" text-anchor="middle" fill="#777777" font-size="12">保持所有权</text>
    <text x="820" y="474" text-anchor="middle" fill="#777777" font-size="12">提交证据</text>
    <text x="1040" y="474" text-anchor="middle" fill="#2F6BFF" font-size="12">逐条确认</text>
    <path d="M834 540 C900 574 978 574 1040 520" fill="none" stroke="#2F6BFF" marker-end="url(#arrow-blue)"/>
    <text x="918" y="590" text-anchor="middle" fill="#2F6BFF" font-size="14">只有 verified 才闭环</text>
    """
    return semantic, shell(semantic["meta"]["title"], semantic["meta"]["subtitle"], body)


def board_03() -> tuple[dict, str]:
    nodes = [
        {"id": "state", "type": "plane", "label": "项目状态", "importance": 2},
        {"id": "artifact", "type": "plane", "label": "真实产物", "importance": 3},
        {"id": "criteria", "type": "plane", "label": "验收标准", "importance": 3},
        {"id": "relay", "type": "cube", "label": "薪尽火传", "importance": 5},
        {"id": "continuity", "type": "circle", "label": "可继续现场", "importance": 4},
    ]
    edges = [
        {"from": "state", "to": "relay", "relation": "记录"},
        {"from": "artifact", "to": "relay", "relation": "指纹"},
        {"from": "criteria", "to": "relay", "relation": "约束"},
        {"from": "relay", "to": "continuity", "relation": "交付"},
    ]
    semantic = scene(
        "03-trust",
        "交的不是摘要，是可验证的现场",
        "事实、产物和验收标准一起过棒",
        "可靠交接必须同时传递状态、产物证据和验收标准",
        "input-process-output",
        "relay",
        nodes,
        edges,
    )
    body = """
    <text x="146" y="216" fill="#999999" font-size="12" letter-spacing="2">输入</text>
    <text x="526" y="216" fill="#2F6BFF" font-size="12" letter-spacing="2">接力协议</text>
    <text x="932" y="216" fill="#999999" font-size="12" letter-spacing="2">输出</text>
    <rect x="116" y="250" width="190" height="64" fill="#F5F5F5" stroke="#B8B8B8"/>
    <rect x="116" y="340" width="190" height="64" fill="#F5F5F5" stroke="#B8B8B8"/>
    <rect x="116" y="430" width="190" height="64" fill="#F5F5F5" stroke="#B8B8B8"/>
    <text x="211" y="288" text-anchor="middle" fill="#111111" font-size="17" font-weight="600">项目状态</text>
    <text x="211" y="378" text-anchor="middle" fill="#111111" font-size="17" font-weight="600">真实产物</text>
    <text x="211" y="468" text-anchor="middle" fill="#111111" font-size="17" font-weight="600">验收标准</text>
    <path d="M306 282 C394 282 410 320 488 340" fill="none" stroke="#222222" marker-end="url(#arrow)"/>
    <path d="M306 372 C388 372 414 372 488 372" fill="none" stroke="#222222" marker-end="url(#arrow)"/>
    <path d="M306 462 C394 462 410 424 488 404" fill="none" stroke="#222222" marker-end="url(#arrow)"/>
    <polygon points="600,230 724,302 724,442 600,514 476,442 476,302" fill="#FFFFFF" stroke="#2F6BFF" stroke-width="1.5"/>
    <circle cx="600" cy="372" r="54" fill="#FFFFFF" stroke="#2F6BFF" stroke-width="1.5"/>
    <circle cx="600" cy="372" r="7" fill="#2F6BFF"/>
    <text x="600" y="364" text-anchor="middle" fill="#111111" font-size="18" font-weight="600">薪尽火传</text>
    <text x="600" y="392" text-anchor="middle" fill="#777777" font-size="12">追加 · 锁定 · 可追溯</text>
    <path d="M724 372 C796 318 846 318 894 354" fill="none" stroke="#2F6BFF" stroke-width="1.5" marker-end="url(#arrow-blue)"/>
    <circle cx="986" cy="372" r="90" fill="#F5F5F5" stroke="#222222"/>
    <text x="986" y="363" text-anchor="middle" fill="#111111" font-size="21" font-weight="600">可继续工作的现场</text>
    <text x="986" y="395" text-anchor="middle" fill="#777777" font-size="13">下一位 Agent 直接开工</text>
    """
    return semantic, shell(semantic["meta"]["title"], semantic["meta"]["subtitle"], body)


def board_04() -> tuple[dict, str]:
    nodes = [
        {"id": "codex", "type": "point", "label": "Codex", "importance": 2},
        {"id": "trae", "type": "point", "label": "TRAE", "importance": 2},
        {"id": "claude", "type": "point", "label": "Claude", "importance": 2},
        {"id": "cursor", "type": "point", "label": "Cursor", "importance": 2},
        {"id": "entry", "type": "plane", "label": "入口层", "importance": 3},
        {"id": "protocol", "type": "plane", "label": "接力协议", "importance": 5},
        {"id": "ledger", "type": "plane", "label": "事件账本", "importance": 4},
    ]
    edges = [
        {"from": agent, "to": "entry", "relation": "接入"}
        for agent in ["codex", "trae", "claude", "cursor"]
    ] + [
        {"from": "entry", "to": "protocol", "relation": "统一动作"},
        {"from": "protocol", "to": "ledger", "relation": "持久化"},
    ]
    semantic = scene(
        "04-architecture",
        "四个 Agent，一套接力协议",
        "各自保留记忆，共享可执行状态",
        "不同 Agent 通过统一入口使用同一接力状态机",
        "layered-architecture",
        "protocol",
        nodes,
        edges,
    )
    body = """
    <text x="120" y="204" fill="#999999" font-size="12" letter-spacing="2">AGENTS</text>
    <line x1="120" y1="238" x2="1080" y2="238" stroke="#E8E8E8"/>
    <circle cx="240" cy="282" r="34" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="480" cy="282" r="34" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="720" cy="282" r="34" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="960" cy="282" r="34" fill="#FFFFFF" stroke="#222222"/>
    <text x="240" y="288" text-anchor="middle" fill="#111111" font-size="15" font-weight="600">Codex</text>
    <text x="480" y="288" text-anchor="middle" fill="#111111" font-size="15" font-weight="600">TRAE</text>
    <text x="720" y="288" text-anchor="middle" fill="#111111" font-size="15" font-weight="600">Claude</text>
    <text x="960" y="288" text-anchor="middle" fill="#111111" font-size="15" font-weight="600">Cursor</text>
    <line x1="240" y1="316" x2="240" y2="350" stroke="#B8B8B8"/>
    <line x1="480" y1="316" x2="480" y2="350" stroke="#B8B8B8"/>
    <line x1="720" y1="316" x2="720" y2="350" stroke="#B8B8B8"/>
    <line x1="960" y1="316" x2="960" y2="350" stroke="#B8B8B8"/>
    <rect x="152" y="350" width="896" height="62" fill="#F5F5F5" stroke="#B8B8B8"/>
    <text x="600" y="387" text-anchor="middle" fill="#111111" font-size="18" font-weight="600">Skill · MCP · SessionStart Hook</text>
    <rect x="216" y="436" width="768" height="74" fill="#FFFFFF" stroke="#2F6BFF" stroke-width="1.5"/>
    <circle cx="250" cy="473" r="7" fill="#2F6BFF"/>
    <text x="600" y="469" text-anchor="middle" fill="#111111" font-size="21" font-weight="600">Agent Relay Protocol</text>
    <text x="600" y="493" text-anchor="middle" fill="#777777" font-size="12">offer · accept · heartbeat · complete · verify</text>
    <rect x="312" y="534" width="576" height="54" fill="#F5F5F5" stroke="#B8B8B8"/>
    <text x="600" y="566" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">Append-only Event Ledger</text>
    <line x1="600" y1="412" x2="600" y2="436" stroke="#222222" marker-end="url(#arrow)"/>
    <line x1="600" y1="510" x2="600" y2="534" stroke="#222222" marker-end="url(#arrow)"/>
    """
    return semantic, shell(semantic["meta"]["title"], semantic["meta"]["subtitle"], body)


def board_05() -> tuple[dict, str]:
    nodes = [
        {"id": "relay", "type": "circle", "label": "接力棒", "importance": 5},
        {"id": "reject", "type": "point", "label": "拒绝", "importance": 2},
        {"id": "fail", "type": "point", "label": "失败", "importance": 2},
        {"id": "expire", "type": "point", "label": "过期", "importance": 2},
        {"id": "cancel", "type": "point", "label": "取消", "importance": 2},
        {"id": "recover", "type": "point", "label": "下一棒", "importance": 4},
    ]
    edges = [
        {"from": "relay", "to": state, "relation": "留痕"}
        for state in ["reject", "fail", "expire", "cancel"]
    ] + [{"from": "relay", "to": "recover", "relation": "重启"}]
    semantic = scene(
        "05-recovery",
        "失败也要留下下一棒",
        "拒绝、失败、过期、取消，都不删历史",
        "接力系统把失败状态变成下一位 Agent 可复用的起点",
        "radial-center",
        "recover",
        nodes,
        edges,
    )
    body = """
    <circle cx="600" cy="354" r="98" fill="#FFFFFF" stroke="#222222"/>
    <circle cx="600" cy="354" r="8" fill="#2F6BFF"/>
    <text x="600" y="338" text-anchor="middle" fill="#111111" font-size="22" font-weight="600">接力状态</text>
    <text x="600" y="382" text-anchor="middle" fill="#777777" font-size="13">事件只追加，不抹掉</text>
    <circle cx="286" cy="246" r="46" fill="#F5F5F5" stroke="#B8B8B8"/>
    <circle cx="286" cy="464" r="46" fill="#F5F5F5" stroke="#B8B8B8"/>
    <circle cx="914" cy="246" r="46" fill="#F5F5F5" stroke="#B8B8B8"/>
    <circle cx="914" cy="464" r="46" fill="#F5F5F5" stroke="#B8B8B8"/>
    <text x="286" y="252" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">拒绝</text>
    <text x="286" y="470" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">失败</text>
    <text x="914" y="252" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">过期</text>
    <text x="914" y="470" text-anchor="middle" fill="#111111" font-size="16" font-weight="600">取消</text>
    <path d="M516 304 C446 252 380 234 332 246" fill="none" stroke="#B8B8B8"/>
    <path d="M516 404 C446 456 380 476 332 464" fill="none" stroke="#B8B8B8"/>
    <path d="M684 304 C754 252 820 234 868 246" fill="none" stroke="#B8B8B8"/>
    <path d="M684 404 C754 456 820 476 868 464" fill="none" stroke="#B8B8B8"/>
    <path d="M600 452 C600 506 600 528 600 558" fill="none" stroke="#2F6BFF" stroke-width="1.5" marker-end="url(#arrow-blue)"/>
    <circle cx="600" cy="582" r="28" fill="#FFFFFF" stroke="#2F6BFF" stroke-width="1.5"/>
    <text x="600" y="587" text-anchor="middle" fill="#2F6BFF" font-size="14" font-weight="600">下一棒</text>
    <text x="600" y="628" text-anchor="middle" fill="#777777" font-size="13">知道为什么停，才能从正确的位置重启</text>
    """
    return semantic, shell(semantic["meta"]["title"], semantic["meta"]["subtitle"], body)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    builders = [board_01, board_02, board_03, board_04, board_05]
    for index, builder in enumerate(builders, 1):
        semantic, svg = builder()
        board_id = semantic["meta"]["id"]
        (OUTPUT / f"{board_id}.scene.json").write_text(
            json.dumps(semantic, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (OUTPUT / f"{board_id}.svg").write_text(svg, encoding="utf-8")
        print(f"{index}: {board_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
