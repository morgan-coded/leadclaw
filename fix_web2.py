with open('leadclaw/web.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = []

# 8. renderLead - find exact boundaries and replace
start_marker = "function renderLead(l,showActions=true){\n"
end_marker = "\nfunction renderList"
start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx)
assert start_idx >= 0 and end_idx >= 0, f"renderLead bounds: {start_idx}, {end_idx}"

new_render_fn = r"""function renderLead(l,showActions=true){
  const due=l.follow_up_after?`<div class="lead-meta ${l.status==='followup_due'?'yellow':''}">${l.follow_up_after}</div>`:'';
  const quote=l.quote_amount?`<div class="lead-meta">${fmt(l.quote_amount)}</div>`:'';
  const contact=[l.phone,l.email].filter(Boolean).join(' \u00b7 ');
  const isActive=!['won','lost','paid'].includes(l.status);
  const lj=esc(JSON.stringify(l));
  let actions='';
  if(showActions){
    if(['won','lost','paid'].includes(l.status)){
      if(l.status==='paid'){
        actions=`<button class="btn btn-sm" onclick='openNextService(${l.id})'>Next Service</button>`;
      }
      actions+=`<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`;
    } else if(l.status==='booked'){
      actions=`<button class="btn btn-sm" onclick='doComplete(${l.id},"${esc(l.name)}")'>Complete</button>`+
        `<button class="btn btn-sm btn-danger" onclick='openLost(${l.id})'>Lost</button>`+
        `<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`;
    } else if(l.status==='completed'){
      const invoiceBtn=`<button class="btn btn-sm" onclick='openInvoice(${l.id},${l.quote_amount||0})'>Invoice</button>`;
      const paidBtn=l.invoice_sent_at?`<button class="btn btn-sm" onclick='doPaid(${l.id},"${esc(l.name)}")'>Mark Paid</button>`:"";
      actions=invoiceBtn+paidBtn+
        `<button class="btn btn-sm btn-danger" onclick='openLost(${l.id})'>Lost</button>`+
        `<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`;
    } else {
      actions=`<button class="btn btn-sm" onclick='openQuote(${l.id})'>Quote</button>`+
        `<button class="btn btn-sm" onclick='openBook(${l.id})'>Book</button>`+
        `<button class="btn btn-sm" onclick='openEdit(JSON.parse(this.dataset.l))' data-l="${lj}">Edit</button>`+
        `<button class="btn btn-sm" onclick='doWon(${l.id},"${esc(l.name)}")'>Won</button>`+
        `<button class="btn btn-sm btn-danger" onclick='openLost(${l.id})'>Lost</button>`+
        `<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`;
    }
  }
  const lostNote=l.lost_reason?`<div class="lead-notes">Lost: ${esc(l.lost_reason)}${l.lost_reason_notes?' \u2014 '+esc(l.lost_reason_notes):''}</div>`:'';
  const extraMeta=[];
  if(l.scheduled_date)extraMeta.push(`Scheduled: ${l.scheduled_date}`);
  if(l.invoice_amount)extraMeta.push(`Invoice: ${fmt(l.invoice_amount)}`);
  if(l.paid_at)extraMeta.push(`Paid: ${l.paid_at}`);
  if(l.next_service_due_at)extraMeta.push(`Next svc: ${l.next_service_due_at}`);
  const extraMetaEl=extraMeta.length?`<div class="lead-notes">${extraMeta.join(' \u00b7 ')}</div>`:'';
  return `<div class="lead" data-id="${l.id}" data-status="${l.status}">
    <div class="lead-body">
      <div class="lead-top"><span class="lead-name">${esc(l.name)}</span>${badge(l.status)}</div>
      <div class="lead-service">${esc(l.service||'')}${contact?' \u00b7 '+esc(contact):''}</div>
      ${l.notes?`<div class="lead-notes">${esc(l.notes)}</div>`:''}
      ${lostNote}
      ${extraMetaEl}
    </div>
    <div class="lead-actions">
      <div>${quote}${due}<div class="lead-meta">#${l.id}</div></div>
      <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end">${actions}</div>
    </div>
  </div>`;
}"""

content = content[:start_idx] + new_render_fn + content[end_idx:]
changes.append("renderLead")

# 9. load() - add reminders rendering
old_load_inner = "    renderList('active',d.active);\n    document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();"
new_load_inner = """    renderList('active',d.active);
    const ir=d.invoice_reminders||[];
    const sr=d.service_reminders||[];
    document.getElementById('invoice-reminders').innerHTML=ir.length?ir.map(l=>renderLead(l,true)).join(''):'<div class="empty">No overdue invoices.</div>';
    document.getElementById('service-reminders').innerHTML=sr.length?sr.map(l=>renderLead(l,true)).join(''):'<div class="empty">No recurring service due.</div>';
    document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();"""
assert old_load_inner in content, "load inner not found"; content = content.replace(old_load_inner, new_load_inner); changes.append("load reminders")

# 10. Add modals before pilot draft modal
old_pilot_modal = "<!-- Pilot draft modal -->"
new_modals = """<!-- Book modal -->
<div class="overlay" id="modal-book" onclick="closeModal(event)">
  <div class="modal">
    <h3>Book Lead</h3>
    <input type="hidden" id="book-id">
    <div class="form-group"><label>Scheduled Date</label><input id="book-date" type="date"></div>
    <div class="err" id="book-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-book')">Cancel</button>
      <button class="btn btn-primary" onclick="submitBook()">Book</button>
    </div>
  </div>
</div>

<!-- Invoice modal -->
<div class="overlay" id="modal-invoice" onclick="closeModal(event)">
  <div class="modal">
    <h3>Send Invoice</h3>
    <input type="hidden" id="invoice-id">
    <div class="form-group"><label>Invoice Amount ($, leave blank to use quote amount)</label><input id="invoice-amount" type="number" min="0.01" step="0.01" placeholder="e.g. 950"></div>
    <div class="err" id="invoice-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-invoice')">Cancel</button>
      <button class="btn btn-primary" onclick="submitInvoice()">Record Invoice</button>
    </div>
  </div>
</div>

<!-- Next Service modal -->
<div class="overlay" id="modal-nextsvc" onclick="closeModal(event)">
  <div class="modal">
    <h3>Set Next Service Date</h3>
    <input type="hidden" id="nextsvc-id">
    <div class="form-group"><label>Next Service Date</label><input id="nextsvc-date" type="date"></div>
    <div class="err" id="nextsvc-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-nextsvc')">Cancel</button>
      <button class="btn btn-primary" onclick="submitNextService()">Set Date</button>
    </div>
  </div>
</div>

<!-- Pilot draft modal -->"""
assert old_pilot_modal in content, "pilot draft modal not found"; content = content.replace(old_pilot_modal, new_modals, 1); changes.append("modals HTML")

# 11. Add JS functions before the last load();
load_marker = "\nload();\n</script>"
idx = content.rfind(load_marker)
assert idx >= 0, "load() marker not found"

new_js = """
// ===========================================================================
// New lifecycle action handlers: Book, Complete, Invoice, Paid, Next Service
// ===========================================================================

function openBook(id){
  document.getElementById('book-id').value=id;
  document.getElementById('book-date').value='';
  document.getElementById('book-err').style.display='none';
  document.getElementById('modal-book').classList.add('open');
}
async function submitBook(){
  const id=document.getElementById('book-id').value;
  const date=document.getElementById('book-date').value;
  const errEl=document.getElementById('book-err');
  if(!date||!validDate(date)){errEl.textContent='Enter a valid date (YYYY-MM-DD).';errEl.style.display='';return;}
  const r=await fetch(`/api/leads/${id}/book`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheduled_date:date})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-book');toast('Lead booked!');load();
}

async function doComplete(id,name){
  if(!confirm('Mark "'+name+'" as completed?'))return;
  const r=await fetch(`/api/leads/${id}/complete`,{method:'POST'});
  if(r.ok){toast('Marked completed.');load();}else{toast('Error',true);}
}

function openInvoice(id,defaultAmount){
  document.getElementById('invoice-id').value=id;
  document.getElementById('invoice-amount').value=defaultAmount||'';
  document.getElementById('invoice-err').style.display='none';
  document.getElementById('modal-invoice').classList.add('open');
}
async function submitInvoice(){
  const id=document.getElementById('invoice-id').value;
  const amount=document.getElementById('invoice-amount').value;
  const errEl=document.getElementById('invoice-err');
  const body={};
  if(amount){
    const a=parseFloat(amount);
    if(isNaN(a)||a<=0){errEl.textContent='Amount must be > 0.';errEl.style.display='';return;}
    body.invoice_amount=a;
  }
  const r=await fetch(`/api/leads/${id}/invoice`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-invoice');toast('Invoice recorded.');load();
}

async function doPaid(id,name){
  if(!confirm('Mark "'+name+'" as PAID?'))return;
  const r=await fetch(`/api/leads/${id}/paid`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recurring_days:90})});
  if(r.ok){toast('Marked paid!');load();}else{toast('Error',true);}
}

function openNextService(id){
  document.getElementById('nextsvc-id').value=id;
  document.getElementById('nextsvc-date').value='';
  document.getElementById('nextsvc-err').style.display='none';
  document.getElementById('modal-nextsvc').classList.add('open');
}
async function submitNextService(){
  const id=document.getElementById('nextsvc-id').value;
  const date=document.getElementById('nextsvc-date').value;
  const errEl=document.getElementById('nextsvc-err');
  if(!date||!validDate(date)){errEl.textContent='Enter a valid date (YYYY-MM-DD).';errEl.style.display='';return;}
  const r=await fetch(`/api/leads/${id}/next-service`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({next_service_due_at:date})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-nextsvc');toast('Next service date set.');load();
}
"""

content = content[:idx] + new_js + content[idx:]
changes.append("JS functions")

print("All changes:", changes)

with open('leadclaw/web.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Part 2 written OK, total length:", len(content))
