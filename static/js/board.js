function boardDragStart(ev){
  ev.dataTransfer.setData('text/contact-id', ev.target.dataset.id);
}
document.addEventListener('dragover', (ev)=>{
  const col = ev.target.closest('.board-col-body');
  document.querySelectorAll('.board-col-body').forEach(n=>n.classList.remove('drag-over'));
  if(col){ col.classList.add('drag-over'); }
});
document.addEventListener('drop', async (ev)=>{
  const col = ev.target.closest('.board-col');
  if(!col) return;
  const targetBody = col.querySelector('.board-col-body');
  targetBody.classList.remove('drag-over');
  const cid = ev.dataTransfer.getData('text/contact-id');
  const newStage = col.dataset.stage;
  try{
    const res = await fetch(`/contact/${cid}/move`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({stage:newStage})
    });
    const data = await res.json();
    if(data.ok){
      // move card in DOM
      const card = document.querySelector(`.card-lead[data-id='${cid}']`);
      if(card){ targetBody.prepend(card); }
    }else{
      alert(data.error || 'Erro ao mover');
    }
  }catch(e){ alert('Falha ao mover'); }
});
