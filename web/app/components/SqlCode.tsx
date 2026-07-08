"use client";

import { useMemo, type ReactNode } from "react";

/**
 * SqlCode — a tiny, dependency-free SQL syntax highlighter (CSP-safe: pure regex
 * tokenization → coloured <span>s, no eval, no external lib). Colours are drawn
 * from the app palette so highlighted SQL reads as part of the product, not a
 * pasted-in code widget. This is the "read the SQL you trust" surface, so it's
 * worth the polish.
 */
const KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "NULL", "IS", "IN", "LIKE", "ILIKE",
  "BETWEEN", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "ON", "USING",
  "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "FETCH", "FIRST", "NEXT", "ROW",
  "ROWS", "ONLY", "AS", "DISTINCT", "ALL", "UNION", "INTERSECT", "EXCEPT", "CASE", "WHEN",
  "THEN", "ELSE", "END", "ASC", "DESC", "WITH", "RECURSIVE", "OVER", "PARTITION", "EXISTS",
  "ANY", "SOME", "VALUES", "CAST", "INTERVAL", "TRUE", "FALSE", "NULLS", "LAST", "FILTER",
  "CURRENT_DATE", "CURRENT_TIMESTAMP", "WINDOW", "GROUPING", "ROLLUP",
]);

// comment | string/quoted-ident | number | word | whitespace | punctuation — covers every char.
const TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)|(\s+)|([^\s\w])/g;

const CLS = {
  comment: "text-slate-400 italic",
  string: "text-emerald-300",
  number: "text-amber-300",
  keyword: "font-medium text-indigo-300",
  func: "text-sky-300",
  punct: "text-slate-400",
  ident: "text-slate-200",
};

function tokenize(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let m: RegExpExecArray | null;
  let key = 0;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(code)) !== null) {
    const [tok, comment, str, num, word, ws] = m;
    let cls = CLS.ident;
    if (comment) cls = CLS.comment;
    else if (str) cls = CLS.string;
    else if (num) cls = CLS.number;
    else if (word) {
      if (KEYWORDS.has(word.toUpperCase())) cls = CLS.keyword;
      else cls = /^\s*\(/.test(code.slice(m.index + tok.length)) ? CLS.func : CLS.ident;
    } else if (ws) {
      out.push(<span key={key++}>{tok}</span>); // preserve whitespace verbatim (pre)
      continue;
    } else {
      cls = CLS.punct;
    }
    out.push(
      <span key={key++} className={cls}>
        {tok}
      </span>,
    );
  }
  return out;
}

export function SqlCode({ code, className = "" }: { code: string; className?: string }) {
  const tokens = useMemo(() => tokenize(code), [code]);
  return (
    <pre
      className={`scrollbar-thin overflow-auto rounded-lg border border-slate-800 bg-slate-950/60 p-4 font-mono text-sm leading-relaxed ${className}`}
    >
      <code>{tokens}</code>
    </pre>
  );
}
