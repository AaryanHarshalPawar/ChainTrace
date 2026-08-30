/**
 * The fund-flow graph.
 *
 * Laid out left-to-right by hop depth using dagre, because the trace is a
 * layered DAG rather than an arbitrary network: hop 0 is where the victim
 * paid, and every column after it is one step further from that payment. A
 * force-directed layout would scramble exactly the information the officer is
 * reading the picture for.
 *
 * Edge thickness encodes traced value, so the branch carrying the money is
 * visible before any label is read.
 */

import { useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import dagre from "dagre";
import "@xyflow/react/dist/style.css";

import type { TraceEdge, TraceNode } from "../api/client";
import { roleStyle, shortAddress } from "../format";

const NODE_W = 208;
const NODE_H = 96;

type CardData = {
  node: TraceNode;
  selected: boolean;
};

function TraceCard({ data }: NodeProps<Node<CardData>>) {
  const { node, selected } = data;
  const style = roleStyle(node.role ?? "intermediary");
  const taint = (node.taint_ratio ?? 0) * 100;

  return (
    <div
      style={{
        width: NODE_W,
        minHeight: NODE_H,
        background: "var(--surface)",
        border: `${style.emphasis ? 2 : 1}px solid ${style.border}`,
        borderLeft: `4px solid ${style.border}`,
        borderRadius: "var(--radius)",
        boxShadow: selected
          ? "0 0 0 3px var(--accent-soft), var(--shadow)"
          : "var(--shadow)",
        padding: "10px 12px",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />

      <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
        <span className="label">HOP {node.depth}</span>
        <span className="pill" style={{ color: style.border }}>
          {style.short}
        </span>
      </div>

      <div
        className="mono"
        style={{ fontSize: 12, marginTop: 6, fontWeight: 500, color: "var(--ink)" }}
      >
        {shortAddress(node.address)}
      </div>

      {node.label && (
        <div
          style={{
            fontSize: 11.5,
            color: "var(--ink-2)",
            marginTop: 3,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={node.label}
        >
          {node.label}
        </div>
      )}

      <div
        className="mono"
        style={{
          fontSize: 10.5,
          color: "var(--muted)",
          marginTop: 6,
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>{taint >= 0.1 ? `taint ${taint.toFixed(0)}%` : "taint <0.1%"}</span>
        {node.stop_reason && <span title={node.stop_reason}>■ stopped</span>}
      </div>

      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { trace: TraceCard };

function layout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 34, ranksep: 96, marginx: 24, marginy: 24 });

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    // dagre reports centres; React Flow positions from the top-left corner.
    return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } };
  });
}

interface Props {
  nodes: TraceNode[];
  edges: TraceEdge[];
  selectedAddress: string | null;
  onSelect: (address: string) => void;
}

export function FlowGraph({ nodes, edges, selectedAddress, onSelect }: Props) {
  const { flowNodes, flowEdges } = useMemo(() => {
    const maxUsd = Math.max(
      1,
      ...edges.map((e) => Number(e.total_usd ?? 0)),
    );

    const rfNodes: Node[] = nodes.map((n) => ({
      id: n.address,
      type: "trace",
      position: { x: 0, y: 0 },
      data: { node: n, selected: n.address === selectedAddress },
    }));

    const rfEdges: Edge[] = edges.map((e, i) => {
      const share = Number(e.total_usd ?? 0) / maxUsd;
      return {
        id: `${e.source}-${e.target}-${e.asset_symbol}-${i}`,
        source: e.source,
        target: e.target,
        animated: share > 0.5,
        label: e.asset_symbol,
        labelStyle: {
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          fill: "var(--muted)",
        },
        labelBgStyle: { fill: "var(--bg)" },
        style: {
          // 1.5px floor keeps a small branch visible rather than hairline.
          strokeWidth: 1.5 + share * 4.5,
          stroke: share > 0.5 ? "var(--accent)" : "var(--line-strong)",
        },
      };
    });

    return { flowNodes: layout(rfNodes, rfEdges), flowEdges: rfEdges };
  }, [nodes, edges, selectedAddress]);

  const handleNodeClick = useCallback(
    (_: unknown, node: Node) => onSelect(node.id),
    [onSelect],
  );

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      onNodeClick={handleNodeClick}
      fitView
      fitViewOptions={{ padding: 0.18 }}
      minZoom={0.2}
      maxZoom={1.6}
      proOptions={{ hideAttribution: false }}
    >
      <Background color="var(--line-strong)" gap={22} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
