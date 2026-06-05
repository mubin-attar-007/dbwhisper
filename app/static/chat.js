(function () {
  const dbFlag = document.getElementById('db_flag');
  const userId = document.getElementById('user_id');
  const sessionId = document.getElementById('session_id');
  const queryForm = document.getElementById('queryForm');
  const nlquery = document.getElementById('nlquery');
  const formatSelect = document.getElementById('format');
  const showSummaryCheckbox = document.getElementById('show_summary');
  const chatWindow = document.getElementById('chat_window');
  const localHistory = document.getElementById('local_history');
  const clearHistoryBtn = document.getElementById('clear_history');

  const STORAGE_KEY = 'sql_insight_local_history_v2';

  function loadLocalHistory() {
    const raw = localStorage.getItem(STORAGE_KEY);
    try {
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      console.warn('local history parse failed', e);
      return [];
    }
  }

  function saveLocalHistory(history) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-10)));
  }

  function renderLocalHistory() {
    const history = loadLocalHistory();
    localHistory.innerHTML = '';
    if (history.length === 0) {
      localHistory.innerHTML = '<div style="padding:0.75rem; color:var(--text-muted); font-size:0.75rem;">No history yet.</div>';
      return;
    }
    history.slice().reverse().forEach((entry) => {
      const el = document.createElement('div');
      el.className = 'local-entry';
      el.innerHTML = `<strong>Q:</strong> ${escapeHtml(entry.query)}<br/><small style="color:var(--text-muted)">${new Date(entry.time).toLocaleTimeString()}</small>`;
      el.onclick = () => {
        nlquery.value = entry.query;
        nlquery.focus();
      };
      localHistory.appendChild(el);
    });
  }

  function escapeHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function createMessageElement(role, contentHtml) {
    const el = document.createElement('div');
    el.className = 'message ' + role;
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = contentHtml;
    el.appendChild(contentDiv);
    return el;
  }

  function appendMessage(role, text) {
    const el = createMessageElement(role, escapeHtml(text).replace(/\n/g, '<br/>'));
    chatWindow.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendHtmlMessage(role, html) {
    const el = createMessageElement(role, html);
    chatWindow.appendChild(el);
    scrollToBottom();
    return el;
  }

  function scrollToBottom() {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  function createDownloadButton(content, filename, mimeType, buttonText) {
    let blobContent = content;
    if (filename.endsWith('.csv')) {
      const BOM = '\uFEFF';
      blobContent = BOM + content;
    }

    const blob = new Blob([blobContent], { type: mimeType + ';charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const linkId = 'download_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

    return `<div style="margin-top:1rem;"><a id="${linkId}" href="${url}" download="${filename}" style="display:inline-block; padding:0.5rem 1rem; background:var(--primary-color); color:white; text-decoration:none; border-radius:0.375rem; font-size:0.875rem; font-weight:500; transition:background 0.2s; cursor:pointer;" onmouseover="this.style.background='var(--primary-hover)'" onmouseout="this.style.background='var(--primary-color)'">${buttonText}</a></div>`;
  }

  function renderTable(csvText) {
    if (!csvText) return '<div style="color:var(--text-muted)">No data returned.</div>';

    const rows = csvText.trim().split(/\r?\n/).map(r => {
      const fields = [];
      let current = '';
      let inQuotes = false;
      for (let i = 0; i < r.length; i++) {
        const char = r[i];
        if (char === '"') {
          inQuotes = !inQuotes;
        } else if (char === ',' && !inQuotes) {
          fields.push(current);
          current = '';
        } else {
          current += char;
        }
      }
      fields.push(current);
      return fields.map(f => f.replace(/^"|"$/g, '').trim());
    });

    if (rows.length === 0) return '';

    let html = '<div class="result-container"><div class="table-wrapper"><table class="table-view"><thead><tr>';
    rows[0].forEach(h => {
      html += `<th>${escapeHtml(h)}</th>`;
    });
    html += '</tr></thead><tbody>';

    const maxRows = Math.min(rows.length - 1, 100);
    for (let i = 1; i <= maxRows; i++) {
      html += '<tr>';
      rows[i].forEach(cell => {
        html += `<td>${escapeHtml(cell)}</td>`;
      });
      html += '</tr>';
    }

    html += '</tbody></table></div></div>';

    if (rows.length > 101) {
      html += `<div style="padding:0.5rem; font-size:0.75rem; color:var(--text-muted); text-align:center;">Showing first 100 rows of ${rows.length - 1} total</div>`;
    }

    return html;
  }

  function renderJSON(obj) {
    let jsonStr = JSON.stringify(obj, null, 2);
    return `<pre><code>${escapeHtml(jsonStr)}</code></pre>`;
  }

  queryForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const q = nlquery.value.trim();
    if (!q) return;

    appendMessage('user', q);
    nlquery.value = '';

    const currentFormat = formatSelect.value || 'table';

    const payload = {
      query: q,
      db_flag: dbFlag.value || 'crm_db',
      output_format: currentFormat === 'table' ? 'csv' : currentFormat,
      user_id: userId.value || undefined,
      session_id: sessionId.value || undefined,
    };

    const loadingMsg = appendMessage('agent', 'Thinking...');

    try {
      const resp = await fetch('/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      loadingMsg.remove();
      const json = await resp.json();

      if (!resp.ok) {
        const errorMsg = json.detail || resp.statusText;
        appendMessage('agent', `❌ Error: ${errorMsg}`);
        return;
      }

      const sql = json.sql || '';
      let agentText = '';

      if (json.natural_summary && (showSummaryCheckbox ? showSummaryCheckbox.checked : true)) {
        const sanitizedSummary = sanitizeSummary(json.natural_summary);
        agentText += `<div class="summary-block" title="Click to expand/collapse">${sanitizedSummary}</div>`;
      }

      // SQL display commented out
      // if (sql) {
      //   agentText += `<div style="margin-bottom:0.5rem; font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em;">Generated SQL</div><pre><code>${escapeHtml(sql)}</code></pre>`;
      // } else {
      //   agentText += '<div style="color:var(--text-muted);">No SQL generated</div>';
      // }

      if (json.follow_up_questions && json.follow_up_questions.length) {
        agentText += `<div style="margin-top:1rem;"><strong style="font-size:0.75rem; text-transform:uppercase; color:var(--text-muted);">💡 Suggested Questions</strong><ul style="margin-top:0.5rem; list-style:none; padding:0;">`;
        json.follow_up_questions.forEach(q => {
          const escapedQ = escapeHtml(q).replace(/'/g, "\\'");
          agentText += `<li style="cursor:pointer; transition:all 0.2s; color:var(--primary-color); padding:0.5rem; margin:0.25rem 0; border-radius:0.375rem; background:rgba(59,130,246,0.05);" onmouseover="this.style.background='rgba(59,130,246,0.15)'; this.style.transform='translateX(4px)';" onmouseout="this.style.background='rgba(59,130,246,0.05)'; this.style.transform='translateX(0)';" onclick="(function(){document.getElementById('nlquery').value='${escapedQ}';document.getElementById('queryForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));})()">${escapeHtml(q)}</li>`;
        });
        agentText += `</ul></div>`;
      }

      appendHtmlMessage('agent', agentText);

      const data = json.data || {};
      let dataHtml = '';

      const rows = json.metadata?.total_rows ?? data.row_count ?? 'N/A';
      const time = json.metadata?.execution_time_ms ? Math.round(json.metadata.execution_time_ms) : 'N/A';
      const metaHtml = `<div class="message-meta">📊 Rows: ${rows} | ⏱️ Time: ${time}ms</div>`;

      if (currentFormat === 'table' || currentFormat === 'csv') {
        if (data.csv) {
          dataHtml = renderTable(data.csv);
          dataHtml += createDownloadButton(data.csv, 'query_results.csv', 'text/csv', '📥 Download CSV');
        } else if (data.raw_json) {
          try {
            dataHtml = renderJSON(JSON.parse(data.raw_json));
          } catch (e) {
            dataHtml = renderJSON(data.raw_json);
          }
        }
      } else if (currentFormat === 'json') {
        if (data.raw_json) {
          try {
            const parsedJson = JSON.parse(data.raw_json);
            dataHtml = renderJSON(parsedJson);
            dataHtml += createDownloadButton(JSON.stringify(parsedJson, null, 2), 'query_results.json', 'application/json', '📥 Download JSON');
          } catch (e) {
            dataHtml = `<pre><code>${escapeHtml(data.raw_json)}</code></pre>`;
            dataHtml += createDownloadButton(data.raw_json, 'query_results.json', 'application/json', '📥 Download JSON');
          }
        } else if (data.csv) {
          dataHtml = renderTable(data.csv);
          dataHtml += createDownloadButton(data.csv, 'query_results.csv', 'text/csv', '📥 Download CSV');
        }
      } else {
        if (data.csv) {
          dataHtml = renderTable(data.csv);
          dataHtml += createDownloadButton(data.csv, 'query_results.csv', 'text/csv', '📥 Download CSV');
        } else if (data.raw_json) {
          try {
            const parsedJson = JSON.parse(data.raw_json);
            dataHtml = renderJSON(parsedJson);
            dataHtml += createDownloadButton(JSON.stringify(parsedJson, null, 2), 'query_results.json', 'application/json', '📥 Download JSON');
          } catch (e) {
            dataHtml = renderJSON(data.raw_json);
          }
        } else {
          dataHtml = renderJSON(data || json);
        }
      }

      if (dataHtml) {
        appendHtmlMessage('agent', dataHtml + metaHtml);
      }

      const localHist = loadLocalHistory();
      localHist.push({
        query: q,
        sql: sql,
        time: new Date().toISOString()
      });
      saveLocalHistory(localHist);
      renderLocalHistory();

    } catch (err) {
      loadingMsg.remove();
      console.error('request failed', err);
      appendMessage('agent', `❌ Request failed: ${err.message}`);
    }
  });

  clearHistoryBtn.addEventListener('click', () => {
    if (confirm('Clear all local history?')) {
      localStorage.removeItem(STORAGE_KEY);
      renderLocalHistory();
    }
  });

  renderLocalHistory();
})();

// Summary utility functions
function sanitizeSummary(raw) {
  if (!raw) return '';
  // Remove LLM internal tokens e.g. <think> </think> and any other '<...>' spans
  let s = raw.replace(/<[^>]*>/g, '');
  // Collapse multiple whitespace and trim
  s = s.replace(/\s+/g, ' ').trim();
  // Limit length to 500 chars to keep UI snappy
  if (s.length > 500) s = s.substring(0, 497) + '...';
  return escapeHtml(s);
}

// Toggle expand/collapse of summary blocks
document.addEventListener('click', (event) => {
  const target = event.target;
  if (target && target.classList && target.classList.contains('summary-block')) {
    target.classList.toggle('expanded');
  }
});
