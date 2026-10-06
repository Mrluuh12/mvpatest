# -*- coding: utf-8 -*-
"""Pagina de cadastro do desmonte, servida pelo servidor de rotas.

Sem CDN: roda na rede da mina, que nao tem internet. Canvas puro, como a
interface do PTX."""

HTML = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Desmonte - area de exclusao</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#101418;color:#e8eef3;
     font-family:"Segoe UI",system-ui,sans-serif;font-size:15px}
header{padding:14px 20px;background:#161c22;border-bottom:2px solid #263038;
       display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{margin:0;font-size:20px;font-weight:600}
header .nota{color:#8fa3b0;font-size:13px}
main{display:grid;grid-template-columns:340px 1fr;gap:18px;padding:18px;
     align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.cartao{background:#161c22;border:1px solid #263038;border-radius:10px;padding:16px}
.cartao h2{margin:0 0 12px;font-size:16px;font-weight:600;color:#cfe0ec}
label{display:block;margin:10px 0 4px;font-size:13px;color:#a8bcc9}
input,select,textarea{width:100%;padding:9px 10px;border-radius:7px;
  border:1px solid #33414c;background:#0d1216;color:#e8eef3;font:inherit}
textarea{resize:vertical;min-height:52px}
button{padding:10px 16px;border:0;border-radius:7px;font:inherit;font-weight:600;
  background:#1d7a4f;color:#fff;cursor:pointer}
button.sec{background:#33414c}
button.perigo{background:#8c2a2a}
button:disabled{background:#2a3138;color:#78868f;cursor:default}
.linha{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.aviso{padding:10px 12px;border-radius:7px;margin:10px 0;font-size:14px;display:none}
.aviso.erro{background:#4a1d1d;border:1px solid #8c3a3a}
.aviso.ok{background:#153a2a;border:1px solid #2f7a55}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #263038}
th{color:#8fa3b0;font-weight:600;font-size:12px;text-transform:uppercase}
.dm{border:1px solid #263038;border-radius:9px;margin-bottom:12px;overflow:hidden}
.dm>.cab{padding:12px 14px;background:#1a2229;display:flex;gap:12px;
  align-items:center;flex-wrap:wrap;cursor:pointer}
.dm .nome{font-weight:600;font-size:16px}
.dm .meta{color:#8fa3b0;font-size:13px}
.dm .corpo{padding:12px 14px;display:none}
.dm.aberto .corpo{display:block}
.pill{padding:3px 9px;border-radius:99px;font-size:12px;font-weight:600}
.pill.erm{background:#6b3fbf}
.pill.movel{background:#8a6a12}
.pill.zero{background:#2a3138;color:#9fb0bb}
.erm{color:#c2aaff}
canvas{width:100%;height:420px;background:#0b0f13;border-radius:9px;display:block}
.legenda{display:flex;gap:16px;flex-wrap:wrap;color:#8fa3b0;font-size:13px;
  margin-top:10px}
.legenda i{display:inline-block;width:12px;height:12px;border-radius:3px;
  margin-right:6px;vertical-align:-1px}
.vazio{color:#8fa3b0;padding:10px 0}
</style>
</head>
<body>

<header>
  <h1>Desmonte &mdash; area de exclusao</h1>
</header>

<main>
  <section class="cartao">
    <h2>Cadastrar ponto</h2>
    <div id="msg" class="aviso"></div>

    <label>Nome</label>
    <input id="nome" placeholder="Nome do desmonte">

    <label>Quando</label>
    <input id="quando" placeholder="25/08 14h">

    <label>Coordenada</label>
    <select id="modo">
      <option value="lista">Lista de pontos</option>
      <option value="grid">Um ponto (E / N)</option>
      <option value="geo">Um ponto (lat / lon)</option>
    </select>

    <div id="campos-lista">
      <label>Pontos &mdash; um por linha</label>
      <textarea id="pontos" style="min-height:150px;font-family:monospace;font-size:13px"
        placeholder="CS_0020_091 (667401.967,7904915.048)
CC_0910_071 (666889.001,7905699.105)
CC_0930_074 (666516.203,7905510.856)"></textarea>
      <div class="meta" id="contaPontos" style="color:#8fa3b0;font-size:13px;
        margin-top:4px">&nbsp;</div>
    </div>

    <div id="campos-grid" style="display:none">
      <label>Este (E)</label>
      <input id="grid_e" inputmode="decimal" placeholder="665139.64">
      <label>Norte (N)</label>
      <input id="grid_n" inputmode="decimal" placeholder="7910269.13">
    </div>

    <div id="campos-geo" style="display:none">
      <label>Latitude</label>
      <input id="lat" inputmode="decimal" placeholder="-18.893508">
      <label>Longitude</label>
      <input id="lon" inputmode="decimal" placeholder="-43.432549">
    </div>

    <label>Raio (m)</label>
    <input id="raio" inputmode="decimal" value="400">

    <label>Observacao (opcional)</label>
    <textarea id="obs"></textarea>

    <div class="linha">
      <button id="salvar">Cadastrar</button>
      <button class="sec" id="limpar">Limpar</button>
    </div>
  </section>

  <section>
    <div class="cartao" style="margin-bottom:18px">
      <h2>Desmontes ativos</h2>
      <div id="lista"><div class="vazio">Carregando...</div></div>
    </div>

    <div class="cartao">
      <h2>Mapa</h2>
      <canvas id="mapa"></canvas>
      <div class="legenda">
        <span><i style="background:#7f93aa"></i>vias</span>
        <span><i style="background:#ff4040"></i>area de exclusao</span>
        <span><i style="background:#c2aaff"></i>ERM / torre</span>
        <span><i style="background:#ffd24d"></i>equipamento</span>
        <span><i style="background:#ff6b6b"></i>dentro</span>
      </div>
    </div>
  </section>
</main>

<script>
var malha=null, frota=[], desmontes=[], sel=null;

function $(id){return document.getElementById(id);}

function pega(url, ok, falha){
  var x=new XMLHttpRequest();
  x.open('GET',url,true); x.timeout=25000;
  x.onreadystatechange=function(){
    if(x.readyState!==4) return;
    if(x.status>=200&&x.status<300){
      try{ ok(JSON.parse(x.responseText)); }catch(e){ if(falha)falha('resposta invalida'); }
    } else if(falha) falha('HTTP '+x.status);
  };
  x.onerror=function(){ if(falha)falha('falha de rede'); };
  x.send();
}

function manda(url, corpo, ok, falha){
  var x=new XMLHttpRequest();
  x.open('POST',url,true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){
    if(x.readyState!==4) return;
    var j=null;
    try{ j=JSON.parse(x.responseText); }catch(e){}
    if(x.status>=200&&x.status<300) ok(j);
    else if(falha) falha((j&&j.erro)||('HTTP '+x.status));
  };
  x.onerror=function(){ if(falha)falha('falha de rede'); };
  x.send(JSON.stringify(corpo));
}

function msg(texto, erro){
  var d=$('msg');
  d.textContent=texto;
  d.className='aviso '+(erro?'erro':'ok');
  d.style.display=texto?'block':'none';
}

$('modo').onchange=function(){
  var v=this.value;
  $('campos-lista').style.display = v==='lista'?'':'none';
  $('campos-grid').style.display  = v==='grid'?'':'none';
  $('campos-geo').style.display   = v==='geo'?'':'none';
};

/* conta as linhas enquanto o operador cola, para ele ver na hora se o
   sistema entendeu todos os furos */
$('pontos').oninput=function(){
  var n=0, ruins=0, linhas=this.value.split('\n');
  for(var i=0;i<linhas.length;i++){
    var t=linhas[i].trim();
    if(!t) continue;
    var nums=t.replace(/[();]/g,' ').match(/-?\d+(?:[.,]\d+)?/g)||[];
    var ok=false;
    if(nums.length>=2){
      var a=parseFloat(String(nums[nums.length-2]).replace(',','.'));
      var b=parseFloat(String(nums[nums.length-1]).replace(',','.'));
      ok = Math.abs(a)>=1000 && Math.abs(b)>=1000;
    }
    if(ok) n++; else ruins++;
  }
  $('contaPontos').textContent = n+' ponto(s) reconhecido(s)'+
    (ruins? '  |  '+ruins+' linha(s) que nao entendi':'');
  $('contaPontos').style.color = ruins? '#e0a030':'#5fbf8f';
};

$('limpar').onclick=function(){
  ['nome','quando','grid_e','grid_n','lat','lon','obs','pontos'].forEach(function(id){$(id).value='';});
  $('contaPontos').innerHTML='&nbsp;';
  $('contaPontos').style.color='#8fa3b0';
  $('raio').value='400'; msg('');
};

$('salvar').onclick=function(){
  var corpo={nome:$('nome').value, quando:$('quando').value,
             raio_m:$('raio').value, observacao:$('obs').value};
  var v=$('modo').value;
  if(v==='lista') corpo.pontos=$('pontos').value;
  else if(v==='grid'){ corpo.grid_e=$('grid_e').value; corpo.grid_n=$('grid_n').value; }
  else { corpo.lat=$('lat').value; corpo.lon=$('lon').value; }
  $('salvar').disabled=true;
  manda('/api/desmontes', corpo, function(j){
    $('salvar').disabled=false;
    var aviso='Cadastrado: '+j.nome+' ('+(j.n_pontos||1)+' ponto(s))';
    if(j.recusadas&&j.recusadas.length)
      aviso+='. ATENCAO: '+j.recusadas.length+' linha(s) ignorada(s): '+
             j.recusadas[0].linha;
    msg(aviso, !!(j.recusadas&&j.recusadas.length));
    $('limpar').onclick();
    carregaDesmontes();
  }, function(e){ $('salvar').disabled=false; msg(e, true); });
};

function removerDesmonte(id, nome){
  if(!confirm('Encerrar "'+nome+'"?')) return;
  manda('/api/desmontes/remover', {id:id}, function(){ carregaDesmontes(); },
        function(e){ msg(e, true); });
}

function carregaDesmontes(){
  pega('/api/desmontes', function(j){
    desmontes=j.desmontes||[];
    desenhaLista();
    desenha();
  }, function(e){ $('lista').innerHTML='<div class="vazio">Falha: '+e+'</div>'; });
}

function esc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function desenhaLista(){
  var d=$('lista');
  if(!desmontes.length){ d.innerHTML='<div class="vazio">Nenhum desmonte ativo.</div>'; return; }
  var h='';
  for(var i=0;i<desmontes.length;i++){
    var x=desmontes[i], a=x.afetados||{dentro:[],perto:[],locais_dentro:[]};
    var nerm=a.n_erms_dentro||0, nmov=a.n_moveis_dentro||0;
    h+='<div class="dm'+(sel===x.id?' aberto':'')+'" data-id="'+x.id+'">';
    h+='<div class="cab" onclick="abre(\''+x.id+'\')">';
    h+='<span class="nome">'+esc(x.nome)+'</span>';
    h+='<span class="meta">'+esc(x.quando||'')+' &middot; raio '+x.raio_m+' m'
     + (x.n_pontos>1? ' &middot; '+x.n_pontos+' pontos':'')+'</span>';
    h+='<span style="flex:1"></span>';
    h+= nerm ? '<span class="pill erm">'+nerm+' ERM</span>' : '';
    h+= nmov ? '<span class="pill movel">'+nmov+' equip.</span>' : '';
    h+= (!nerm&&!nmov) ? '<span class="pill zero">area livre</span>' : '';
    h+='</div><div class="corpo">';
    if(a.dentro.length){
      h+='<table><tr><th>Nome</th><th>Tipo</th><th>Distancia</th></tr>';
      for(var k=0;k<a.dentro.length;k++){
        var r=a.dentro[k];
        h+='<tr><td class="'+(r.movel?'':'erm')+'">'+esc(r.nome)+'</td><td>'
         + esc(r.tipo)+'</td><td>'+r.distancia_m+' m</td></tr>';
      }
      h+='</table>';
    } else {
      h+='<div class="vazio">Nada dentro do raio.</div>';
    }
    if(a.perto&&a.perto.length){
      h+='<div style="margin-top:10px"><b>Logo fora</b><table>';
      for(var p=0;p<a.perto.length;p++)
        h+='<tr><td class="'+(a.perto[p].movel?'':'erm')+'">'+esc(a.perto[p].nome)
         + '</td><td>'+a.perto[p].distancia_m+' m</td></tr>';
      h+='</table></div>';
    }
    if(a.locais_dentro&&a.locais_dentro.length){
      var nomes=[];
      for(var q=0;q<Math.min(a.locais_dentro.length,12);q++) nomes.push(esc(a.locais_dentro[q].nome));
      h+='<div style="margin-top:10px"><b>Locais no raio ('+a.locais_dentro.length+')</b><br>'
       + '<span class="meta">'+nomes.join(', ')
       + (a.locais_dentro.length>12?', ...':'')+'</span></div>';
    }
    h+='<div class="meta" style="margin-top:10px">Centro: '+x.lat+', '+x.lon
     + (x.grid_e!=null? ' &nbsp;|&nbsp; grid '+x.grid_e+' / '+x.grid_n : '')+'</div>';
    if(x.observacao) h+='<div class="meta" style="margin-top:6px">'+esc(x.observacao)+'</div>';
    h+='<div class="linha"><button class="perigo" onclick="removerDesmonte(\''+x.id
     + '\',\''+esc(x.nome).replace(/'/g,"")+'\')">Encerrar desmonte</button></div>';
    h+='</div></div>';
  }
  d.innerHTML=h;
}

function abre(id){ sel = (sel===id? null : id); desenhaLista(); desenha(); }

/* ------------------------------ mapa ------------------------------ */
var C=$('mapa'), g=C.getContext('2d'), vista=null;

function dim(){
  var r=window.devicePixelRatio||1;
  C.width=C.clientWidth*r; C.height=C.clientHeight*r;
  g.setTransform(r,0,0,r,0,0);
  desenha();
}
window.addEventListener('resize',dim);

function calculaVista(){
  var a=1e9,b=-1e9,c=1e9,d=-1e9, i, j, co;
  if(malha) for(i=0;i<malha.features.length;i++){
    co=malha.features[i].geometry.coordinates;
    for(j=0;j<co.length;j++){
      if(co[j][0]<a)a=co[j][0]; if(co[j][0]>b)b=co[j][0];
      if(co[j][1]<c)c=co[j][1]; if(co[j][1]>d)d=co[j][1];
    }
  }
  if(a>b) return null;
  var cx=(a+b)/2, cy=(c+d)/2;
  var k=Math.cos(cy*Math.PI/180);
  var esc=Math.min(C.clientWidth/((b-a)*k), C.clientHeight/(d-c))*0.88;
  return {cx:cx, cy:cy, esc:esc, k:k};
}

function px(lon){ return (lon-vista.cx)*vista.esc*vista.k + C.clientWidth/2; }
function py(lat){ return -(lat-vista.cy)*vista.esc + C.clientHeight/2; }
/* metros -> pixels, pela latitude do centro */
function mpx(m){ return m/110540.0*vista.esc; }

function desenha(){
  g.fillStyle='#0b0f13'; g.fillRect(0,0,C.clientWidth,C.clientHeight);
  if(!vista) vista=calculaVista();
  if(!vista) return;
  var i,j,co;
  if(malha) for(i=0;i<malha.features.length;i++){
    co=malha.features[i].geometry.coordinates;
    g.beginPath();
    for(j=0;j<co.length;j++){
      var x=px(co[j][0]), y=py(co[j][1]);
      if(j) g.lineTo(x,y); else g.moveTo(x,y);
    }
    g.strokeStyle=malha.features[i].properties.fechada?'#7a2a2a':'#7f93aa';
    g.lineWidth=1.4; g.stroke();
  }

  var dentro={};
  for(i=0;i<desmontes.length;i++){
    var x0=desmontes[i];
    if(sel && x0.id!==sel) continue;
    var pts=(x0.pontos&&x0.pontos.length)?x0.pontos:[x0];
    var r=mpx(x0.raio_m), q;
    for(q=0;q<pts.length;q++){
      var qx=px(pts[q].lon), qy=py(pts[q].lat);
      g.beginPath(); g.arc(qx,qy,r,0,6.2832);
      g.fillStyle='rgba(255,64,64,.13)'; g.fill();
      g.strokeStyle='#ff4040'; g.lineWidth=2; g.stroke();
      g.beginPath(); g.arc(qx,qy,4,0,6.2832); g.fillStyle='#ff4040'; g.fill();
    }
    var cx=px(x0.lon), cy=py(x0.lat);
    g.fillStyle='#ffb3b3'; g.font='bold 12px sans-serif';
    g.fillText(x0.nome+(pts.length>1?' ('+pts.length+' pontos)':''), cx+r+6, cy-4);
    var a=x0.afetados||{dentro:[]};
    for(j=0;j<a.dentro.length;j++) dentro[a.dentro[j].nome]=1;
  }

  for(i=0;i<frota.length;i++){
    var e=frota[i];
    if(!e.tem_fix) continue;
    var ex=px(e.lon), ey=py(e.lat);
    var cor = dentro[e.nome] ? '#ff6b6b' : (e.movel?'#ffd24d':'#c2aaff');
    g.beginPath(); g.arc(ex,ey, dentro[e.nome]?6:4, 0, 6.2832);
    g.fillStyle=cor; g.fill();
    if(dentro[e.nome]){
      g.strokeStyle='#fff'; g.lineWidth=1.5; g.stroke();
      g.fillStyle='#ffd9d9'; g.font='bold 11px sans-serif';
      g.fillText(e.nome, ex+8, ey-6);
    }
  }
}

/* ------------------------------ carga ------------------------------ */
pega('/api/malha', function(j){ malha=j; vista=null; desenha(); }, function(){});
pega('/api/equipamentos', function(j){ frota=j.equipamentos||[]; desenha(); }, function(){});
carregaDesmontes();
dim();
setInterval(function(){
  pega('/api/equipamentos', function(j){ frota=j.equipamentos||[]; desenha(); }, function(){});
  carregaDesmontes();
}, 15000);
</script>
</body>
</html>
"""
