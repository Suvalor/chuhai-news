const zh = document.documentElement.lang === 'zh-CN';
const shape = (rows, cols) => zh ? `${rows} 行 · ${cols} 列` : `Rows: ${rows} · Columns: ${cols}`;
const sourceInput = document.getElementById('sourceInput');
const inputPreview = document.getElementById('inputPreview');
const formulaList = document.getElementById('formulaList');
const formulaMeta = document.getElementById('formulaMeta');
const outputPreview = document.getElementById('outputPreview');
const inputMeta = document.getElementById('inputMeta');
const inputListMeta = document.getElementById('inputListMeta');
const outputMeta = document.getElementById('outputMeta');
const copyButton = document.getElementById('copyButton');
const copyStatus = document.getElementById('copyStatus');
let outputRows = [];
let formulaRows = [];

function parseClipboardTable(value) {
  const normalized = value.replace(/\r\n?/g, '\n').replace(/\n$/, '');
  if (!normalized) return [];

  const rows = normalized.split('\n').map((row) => row.split('\t'));
  const maxColumns = Math.max(...rows.map((row) => row.length));
  return rows.map((row) => [
    ...row,
    ...Array(Math.max(0, maxColumns - row.length)).fill('')
  ]);
}

function isNumericCell(value) {
  const normalized = value.trim().replace(/,/g, '');
  return normalized !== '' && Number.isFinite(Number(normalized));
}

function formatNumber(value) {
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toFixed(10)));
}

function splitNames(value) {
  return value
    .split('/')
    .map((name) => name.trim())
    .filter(Boolean);
}

function equalRatios(count) {
  if (!count) return [];
  const ratio = 1 / count;
  return Array.from({ length: count }, () => ratio);
}

function createFormulaRows(rows) {
  return rows.map((row) => {
    const names = splitNames(row[0] || '');
    return {
      source: row,
      names,
      ratios: equalRatios(names.length)
    };
  });
}

function buildTable(rows) {
  if (!rows.length) return null;

  const shell = document.createElement('div');
  shell.className = 'table-shell';
  const table = document.createElement('table');
  table.className = 'paired-table input-data-table';
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');

  rows[0].forEach((_, index) => {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.textContent = String.fromCharCode(65 + index);
    headRow.appendChild(cell);
  });

  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement('tbody');
  rows.forEach((row) => {
    const tableRow = document.createElement('tr');
    row.forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      tableRow.appendChild(cell);
    });
    body.appendChild(tableRow);
  });

  table.appendChild(body);
  shell.appendChild(table);
  return shell;
}

function renderPreview(container, rows, emptyText) {
  container.className = '';
  container.replaceChildren();
  const table = buildTable(rows);

  if (table) {
    container.appendChild(table);
  } else {
    container.className = 'empty-state';
    container.textContent = emptyText;
  }
}

function updateInputPreview() {
  const rows = parseClipboardTable(sourceInput.value);
  const columns = rows[0]?.length || 0;
  inputMeta.textContent = shape(rows.length, columns);
  inputListMeta.textContent = shape(rows.length, columns);
  renderPreview(inputPreview, rows, (zh ? '粘贴 Excel 数据后，此处显示行列预览。' : 'Paste Excel data to preview its rows and columns.'));
  formulaRows = createFormulaRows(rows);
  renderFormulaRows();
  clearOutput();
}

function renderFormulaRows() {
  formulaList.replaceChildren();
  const validRows = formulaRows.filter((row) => row.names.length);
  formulaMeta.textContent = zh ? `${validRows.length} 行待分配` : `Rows to allocate: ${validRows.length}`;

  if (!validRows.length) {
    formulaList.className = 'empty-state';
    formulaList.textContent = (zh ? '粘贴数据后，此处生成逐行分配公式。' : 'Paste data to see the allocation for each row.');
    return;
  }

  formulaList.className = 'formula-list';
  const table = document.createElement('table');
  table.className = 'paired-table formula-data-table';
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  const maxColumns = Math.max(...validRows.map((row) => row.source.length));

  Array.from({ length: maxColumns }, (_, index) => {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.textContent = String.fromCharCode(65 + index);
    headRow.appendChild(cell);
  });

  head.appendChild(headRow);
  table.appendChild(head);
  const body = document.createElement('tbody');

  formulaRows.forEach((formulaRow, rowIndex) => {
    const row = document.createElement('tr');
    const formulaCell = document.createElement('td');
    const formulaContent = document.createElement('div');
    formulaContent.className = 'formula-cell';
    const total = document.createElement('span');
    total.className = 'formula-total';
    total.textContent = (zh ? '合计 = 1' : 'Total = 1');

    const members = document.createElement('div');
    members.className = 'formula-members';
    if (!formulaRow.names.length) {
      const emptyName = document.createElement('span');
      emptyName.className = 'member-name';
      emptyName.textContent = (zh ? '未识别' : 'No name');
      members.appendChild(emptyName);
    }
    formulaRow.names.forEach((name, memberIndex) => {
      const member = document.createElement('label');
      member.className = 'member-ratio';

      const nameText = document.createElement('span');
      nameText.className = 'member-name';
      nameText.textContent = name;

      const input = document.createElement('input');
      input.className = 'ratio-input';
      input.type = 'number';
      input.min = '0';
      input.max = '1';
      input.step = 'any';
      input.readOnly = formulaRow.names.length === 1;
      input.value = formatNumber(formulaRow.ratios[memberIndex]);
      input.setAttribute('aria-label', zh ? `第 ${rowIndex + 1} 行 ${name} 的比例` : `Ratio for ${name}, row ${rowIndex + 1}`);
      input.addEventListener('input', () => {
        rebalanceRatios(rowIndex, memberIndex, input.value);
      });

      member.append(nameText, input);
      members.appendChild(member);
    });

    formulaContent.append(members, total);
    formulaCell.appendChild(formulaContent);
    row.appendChild(formulaCell);

    formulaRow.source.slice(1).forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.appendChild(cell);
    });

    for (let index = formulaRow.source.length; index < maxColumns; index += 1) {
      row.appendChild(document.createElement('td'));
    }

    body.appendChild(row);
  });

  table.appendChild(body);
  formulaList.appendChild(table);
  scheduleRowHeightSync();
}

function syncPairedRowHeights() {
  const inputRows = inputPreview.querySelectorAll('tbody tr');
  const formulaTableRows = formulaList.querySelectorAll('tbody tr');
  const rowCount = Math.min(inputRows.length, formulaTableRows.length);

  inputRows.forEach((row) => {
    row.style.height = '';
  });
  formulaTableRows.forEach((row) => {
    row.style.height = '';
  });

  if (window.matchMedia('(max-width: 980px)').matches) return;

  for (let index = 0; index < rowCount; index += 1) {
    const height = Math.max(
      inputRows[index].getBoundingClientRect().height,
      formulaTableRows[index].getBoundingClientRect().height
    );
    inputRows[index].style.height = `${height}px`;
    formulaTableRows[index].style.height = `${height}px`;
  }
}

function scheduleRowHeightSync() {
  window.requestAnimationFrame(syncPairedRowHeights);
}

function rebalanceRatios(rowIndex, changedIndex, rawValue) {
  const row = formulaRows[rowIndex];
  if (!row || row.ratios.length < 2) return;

  const changedValue = Math.min(1, Math.max(0, Number(rawValue) || 0));
  const remainingCount = row.ratios.length - 1;
  const remainingValue = remainingCount ? (1 - changedValue) / remainingCount : 0;
  row.ratios = row.ratios.map((_, index) => (
    index === changedIndex ? changedValue : remainingValue
  ));

  const tableRow = formulaList.querySelectorAll('tbody tr')[rowIndex];
  const inputs = tableRow?.querySelectorAll('.ratio-input') || [];
  inputs.forEach((input, index) => {
    if (index !== changedIndex) {
      input.value = formatNumber(row.ratios[index]);
    }
  });

  clearOutput();
}

function clearOutput() {
  outputRows = [];
  copyStatus.textContent = '';
  outputMeta.textContent = (zh ? '等待转换' : 'Awaiting conversion');
  document.getElementById('rowCount').textContent = '0';
  document.getElementById('columnCount').textContent = '0';
  document.getElementById('numberCount').textContent = '0';
  copyButton.disabled = true;
  renderPreview(outputPreview, [], (zh ? '完成转换后，此处显示输出结果。' : 'The converted results will appear here.'));
}

function convertData() {
  if (!formulaRows.length) {
    sourceInput.focus();
    return;
  }

  const summary = new Map();
  let maxColumns = 1;

  formulaRows.forEach((formulaRow) => {
    formulaRow.names.forEach((name, memberIndex) => {
      const ratio = formulaRow.ratios[memberIndex];
      const allocated = formulaRow.source.slice(1).map((cell) => {
        if (!isNumericCell(cell)) return cell;
        return Number(cell.trim().replace(/,/g, '')) * ratio;
      });
      maxColumns = Math.max(maxColumns, allocated.length + 1);

      if (!summary.has(name)) {
        summary.set(name, Array.from({ length: allocated.length }, () => ''));
      }

      const current = summary.get(name);
      allocated.forEach((value, index) => {
        if (typeof value === 'number') {
          current[index] = (typeof current[index] === 'number' ? current[index] : 0) + value;
        } else if (current[index] === '') {
          current[index] = value;
        }
      });
    });
  });

  outputRows = Array.from(summary, ([name, values]) => [
    name,
    ...values.map((value) => typeof value === 'number' ? formatNumber(value) : value)
  ]);
  const convertedNumbers = outputRows.reduce((count, row) => (
    count + row.slice(1).filter(isNumericCell).length
  ), 0);

  renderPreview(outputPreview, outputRows, (zh ? '完成转换后，此处显示输出结果。' : 'The converted results will appear here.'));
  document.getElementById('rowCount').textContent = outputRows.length;
  document.getElementById('columnCount').textContent = maxColumns;
  document.getElementById('numberCount').textContent = convertedNumbers;
  outputMeta.textContent = shape(outputRows.length, maxColumns);
  copyStatus.textContent = '';
  copyButton.disabled = outputRows.length === 0;
}

async function copyOutput() {
  if (!outputRows.length) return;
  const text = outputRows.map((row) => row.join('\t')).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.textContent = zh ? '已复制' : 'Copied';
  } catch {
    copyStatus.textContent = zh ? '复制失败，请选中结果复制' : 'Copy failed. Select the results to copy.';
  }
}

function resetPage() {
  sourceInput.value = '';
  formulaRows = [];
  inputMeta.textContent = shape(0, 0);
  inputListMeta.textContent = shape(0, 0);
  formulaMeta.textContent = zh ? '0 行待分配' : 'Rows to allocate: 0';
  renderPreview(inputPreview, [], (zh ? '粘贴 Excel 数据后，此处显示行列预览。' : 'Paste Excel data to preview its rows and columns.'));
  formulaList.className = 'empty-state';
  formulaList.textContent = (zh ? '粘贴数据后，此处生成逐行分配公式。' : 'Paste data to see the allocation for each row.');
  clearOutput();
  sourceInput.focus();
}

sourceInput.addEventListener('input', updateInputPreview);
window.addEventListener('resize', scheduleRowHeightSync);
document.getElementById('convertButton').addEventListener('click', convertData);
document.getElementById('resetButton').addEventListener('click', resetPage);
copyButton.addEventListener('click', copyOutput);
