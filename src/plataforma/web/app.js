/* Interface da plataforma.
 *
 * Sem etapa de build, de propósito: a barreira para alguém da equipe abrir e
 * corrigir precisa ser baixa. Migrar para um framework depois é local.
 *
 * A regra que atravessa este arquivo: a tela nunca inventa número. Quando não
 * há coletor, ela diz *por que* não há — lendo /api/v1/sinais — e quando um
 * dispositivo não foi sondado, isso é visualmente diferente de "sondado e não
 * respondeu". Traço mudo confunde; zero mente.
 */
(() => {
  "use strict";

  const api = async (rota) => {
    const r = await fetch(rota);
    if (!r.ok) throw new Error(`${rota}: ${r.status}`);
    return r.json();
  };
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const NOME_PAPEL = {
    radio_mesh: "rádio malha", radio_ptp: "rádio PtP", radio_ptmp: "rádio PtMP",
    ihm_bordo: "IHM de bordo", hub_ptx: "hub PTX", gateway_pneu: "gateway pneu",
    gps: "GPS", endpoint_imx: "endpoint IMX", plc: "CLP", conversor_can: "conversor CAN",
    sensor_peso: "sensor de peso", roteador: "roteador", switch: "switch",
    camera: "câmera", ups: "UPS", servidor: "servidor", periferico: "periférico",
    desconhecido: "não reconhecido",
  };
  const NOME_FROTA = {
    CA: "Caminhões", EH: "Escavadeiras", PF: "Perfuratrizes", PA: "Pás carregadeiras",
    TT: "Tratores de esteira", CP: "Comboio", ERB: "Estações base", ERM: "Estações móveis",
    GST: "Gate station", MA: "Motoniveladoras", TN: "Tanques",
  };
  const SELO_ZONA = { corporativa: "neutro", ot_nivel3: "warn", ot_nivel2: "crit" };

  const estado = {
    ativos: [], sinais: [], achados: {}, resumo: {},
    selecionado: null, filtro: "", abertos: new Set(["CA"]), secao: "ativos",
    fichas: new Map(),
  };

  /* -------- estado de um dispositivo: três situações, não duas ---------- */
  function situacao(d) {
    if (d.alcancavel === null || d.alcancavel === undefined)
      return { classe: "", selo: "neutro", texto: "não sondado" };
    if (d.alcancavel)
      return { classe: "ok", selo: "good", texto: "responde" };
    return {
      classe: d.qualidade === "incerta" ? "parcial" : "mau",
      selo: d.qualidade === "incerta" ? "warn" : "crit",
      texto: d.qualidade === "incerta" ? "sem resposta (incerto)" : "sem resposta",
    };
  }

  function pontoDoAtivo(ficha) {
    if (!ficha) return "";
    const sondados = ficha.dispositivos.filter((d) => d.alcancavel !== null);
    if (!sondados.length) return "";
    const vivos = sondados.filter((d) => d.alcancavel).length;
    if (vivos === sondados.length) return "ok";
    return vivos === 0 ? "mau" : "parcial";
  }

  /* ---------------------------- cabeçalho ------------------------------- */
  function pintarTopo() {
    const r = estado.resumo;
    const achados = Object.values(estado.achados).reduce(
      (t, v) => t + (Array.isArray(v) ? v.length : 0), 0);
    document.getElementById("tiles").innerHTML = [
      ["ativos", r.ativos ?? 0, "acc"],
      ["dispositivos", r.dispositivos ?? 0, ""],
      ["sondados", r.sondados ?? 0, ""],
      ["alcançáveis", r.alcancaveis ?? 0, (r.alcancaveis ?? 0) > 0 ? "good" : "warn"],
      ["achados", achados, achados ? "warn" : ""],
    ].map(([t, v, c]) => `<div class="tile ${c}"><b>${v}</b><span>${t}</span></div>`).join("");

    const m = r.modulos || {};
    const partes = Object.entries(m).map(([nome, s]) =>
      `${nome}: ${s.alvos_total - s.alvos_falha}/${s.alvos_total} em ${s.duracao_s}s`);
    document.getElementById("sub").textContent =
      partes.length ? partes.join(" · ") : "nenhum módulo reportou ainda";
  }

  /* ------------------------------ árvore -------------------------------- */
  function ativosVisiveis() {
    if (!estado.filtro) return estado.ativos;
    const f = estado.filtro.toLowerCase();
    return estado.ativos.filter((a) => a.ativo_id.toLowerCase().includes(f));
  }

  function pintarArvore() {
    const grupos = new Map();
    for (const a of ativosVisiveis()) {
      if (!grupos.has(a.frota)) grupos.set(a.frota, []);
      grupos.get(a.frota).push(a);
    }
    const alvo = document.getElementById("arvore");
    if (!grupos.size) { alvo.innerHTML = `<p class="aviso">nada encontrado</p>`; return; }
    alvo.innerHTML = [...grupos.entries()].map(([fr, lista]) => {
      const aberto = estado.abertos.has(fr) || !!estado.filtro;
      const itens = aberto ? `<ul>${lista.map((a) => {
        const cls = pontoDoAtivo(estado.fichas.get(a.ativo_id));
        return `<li><button data-ativo="${esc(a.ativo_id)}"
          aria-current="${a.ativo_id === estado.selecionado}">
          <span class="ponto ${cls}"></span>${esc(a.ativo_id)}
          <span class="qtd">${a.dispositivos.length}</span></button></li>`;
      }).join("")}</ul>` : "";
      return `<div class="grupo"><button data-frota="${esc(fr)}">
        <span class="seta">${aberto ? "▼" : "▶"}</span>${esc(NOME_FROTA[fr] || fr)}
        <span class="cont">${lista.length}</span></button>${itens}</div>`;
    }).join("");
  }

  /* ------------------------------- ficha -------------------------------- */
  function pintarFicha(ficha) {
    const { ativo: a, dispositivos: ds, sinais } = ficha;
    const semColetor = sinais.filter((s) => !s.disponivel);

    const chips = ds.map((d) => {
      const s = situacao(d);
      return `<div class="chip z-${esc(d.zona)}">
        <div class="p">${esc(NOME_PAPEL[d.papel] || d.papel)}</div>
        <div class="n">${esc(d.nome)}</div>
        <div class="e"><span class="selo ${s.selo}">${s.texto}</span></div>
      </div>`;
    }).join("");

    const linhas = ds.map((d) => {
      const s = situacao(d);
      const lat = d.latencia_ms === null || d.latencia_ms === undefined
        ? `<td class="vazio">—</td>`
        : `<td class="d">${d.latencia_ms.toFixed(2)} ms</td>`;
      const perda = d.perda_pct === null || d.perda_pct === undefined
        ? `<td class="vazio">—</td>`
        : `<td class="d">${d.perda_pct.toFixed(0)}%</td>`;
      return `<tr>
        <td class="n">${esc(d.nome)}</td>
        <td class="d">${esc(NOME_PAPEL[d.papel] || d.papel)}</td>
        <td class="d">${esc(d.ip || "—")}</td>
        <td><span class="selo ${SELO_ZONA[d.zona] || "neutro"}">${esc(d.zona)}</span></td>
        <td><span class="selo ${d.identidade === "mac" ? "good" : "warn"}">${esc(d.identidade)}</span></td>
        <td><span class="selo ${s.selo}">${s.texto}</span></td>
        ${lat}${perda}
      </tr>`;
    }).join("");

    const naoSondados = ds.filter((d) => d.alcancavel === null).length;
    document.getElementById("centro").innerHTML = `
      <div class="ficha">
        <h1>${esc(a.ativo_id)}</h1>
        <span class="selo acc">${esc(NOME_FROTA[a.frota] || a.frota)}</span>
        <span class="meta"><b>função</b> ${esc(a.funcao_negocio)}</span>
        <span class="meta"><b>dispositivos</b> ${ds.length}</span>
        <span class="meta"><b>arestas</b> ${ds.length} embarcado_em</span>
      </div>

      <h2 class="secao">O que está embarcado</h2>
      <div class="diagrama">
        <div class="meio"><div class="no"><span>ativo</span>${esc(a.ativo_id)}</div></div>
        <div class="trilho"></div>
        <div class="chips">${chips}</div>
      </div>
      <p class="nota">Cada peça está ligada ao ativo por uma aresta
        <b>embarcado_em</b> — a relação que junta rádio, pneu, CLP e GPS numa
        máquina só. Barra amarela é zona OT; vermelha é zona proibida a módulos.</p>

      <h2 class="secao">Dispositivos</h2>
      <div class="quadro"><table>
        <thead><tr><th>nome</th><th>papel</th><th>IP</th><th>zona</th>
          <th>identidade</th><th>estado</th><th>latência</th><th>perda</th></tr></thead>
        <tbody>${linhas}</tbody></table></div>
      <p class="nota">
        ${naoSondados ? `<b>${naoSondados}</b> de ${ds.length} não foram sondados —
          estão em zona OT, e coleta ativa ali é decisão de outro marco.
          “Não sondado” não é o mesmo que “sem resposta”. ` : ""}
        ${semColetor.length ? `Sem coletor ainda:
          ${semColetor.map((s) => `<b>${esc(s.familia)}</b> (${esc(s.motivo)})`).join(", ")}.` : ""}
      </p>`;
  }

  /* --------------------------- outras seções ---------------------------- */
  function pintarModulos() {
    const m = estado.resumo.modulos || {};
    const linhas = Object.entries(m).map(([nome, s]) => `<tr>
      <td class="d">${esc(nome)}</td>
      <td class="d">${s.alvos_total}</td>
      <td class="d">${s.alvos_falha}</td>
      <td class="d">${s.duracao_s} s</td>
      <td class="d">${s.rejeitadas}</td>
      <td>${s.ultima_coleta_ok
        ? `<span class="selo good">${esc(s.ultima_coleta_ok.slice(11, 19))}</span>`
        : `<span class="selo crit">nunca</span>`}</td></tr>`).join("");
    document.getElementById("centro").innerHTML = `
      <div class="ficha"><h1>Módulos</h1>
        <span class="meta">a plataforma se observa antes de observar o resto</span></div>
      <div class="quadro"><table>
        <thead><tr><th>módulo</th><th>alvos</th><th>falhas</th><th>duração</th>
          <th>recusadas</th><th>última coleta ok</th></tr></thead>
        <tbody>${linhas || `<tr><td colspan="6" class="vazio">nenhum módulo reportou</td></tr>`}</tbody>
      </table></div>
      <p class="nota">Repare na distinção: um módulo com <b>muitas falhas de alvo</b> e
        carimbo de última coleta presente funcionou — foram os equipamentos que não
        responderam. Sem carimbo, quem não funcionou foi o módulo. É a diferença entre
        “perguntei e está ruim” e “não consegui perguntar”.</p>`;
  }

  function pintarSinais() {
    const linhas = estado.sinais.map((s) => `<tr>
      <td class="d">${esc(s.familia)}</td>
      <td>${s.disponivel ? `<span class="selo good">coletando</span>`
                         : `<span class="selo neutro">sem coletor</span>`}</td>
      <td class="d">${esc(s.motivo || "—")}</td></tr>`).join("");
    document.getElementById("centro").innerHTML = `
      <div class="ficha"><h1>Cobertura</h1>
        <span class="meta">o que já tem coletor, e por que o resto não tem</span></div>
      <div class="quadro"><table>
        <thead><tr><th>família</th><th>estado</th><th>motivo</th></tr></thead>
        <tbody>${linhas}</tbody></table></div>
      <p class="nota">Esta tela existe para que uma lacuna seja <b>informação</b>, não
        mistério. Um campo vazio na ficha do ativo tem sempre uma explicação aqui.</p>`;
  }

  function pintarAchados() {
    const titulos = {
      conflitos: "MACs repetidos entre ativos", homonimos: "Homônimos desambiguados",
      divergencias: "Nome discorda do endereço", papel_desconhecido: "Papel não reconhecido",
      fora_do_padrao: "Nome fora do padrão",
    };
    const blocos = Object.entries(titulos).map(([chave, titulo]) => {
      const itens = estado.achados[chave] || [];
      if (!itens.length) return "";
      return `<h2 class="secao">${titulo} — ${itens.length}</h2>
        <div class="quadro"><table><tbody>
        ${itens.slice(0, 25).map((i) => `<tr><td class="n">${esc(i)}</td></tr>`).join("")}
        ${itens.length > 25 ? `<tr><td class="vazio">… mais ${itens.length - 25}</td></tr>` : ""}
        </tbody></table></div>`;
    }).join("");
    document.getElementById("centro").innerHTML = `
      <div class="ficha"><h1>Qualidade do cadastro</h1>
        <span class="meta">o trabalho humano que sobrou depois da derivação</span></div>
      ${blocos || `<p class="aviso">nenhum achado</p>`}`;
  }

  /* ------------------------------ fluxo --------------------------------- */
  async function abrirAtivo(id) {
    estado.selecionado = id;
    pintarArvore();
    if (!estado.fichas.has(id)) {
      estado.fichas.set(id, await api(`/api/v1/ativos/${encodeURIComponent(id)}`));
    }
    pintarFicha(estado.fichas.get(id));
  }

  function pintarSecao() {
    if (estado.secao === "modulos") return pintarModulos();
    if (estado.secao === "sinais") return pintarSinais();
    if (estado.secao === "achados") return pintarAchados();
    return estado.selecionado ? abrirAtivo(estado.selecionado) : undefined;
  }

  async function atualizar() {
    const [resumo, ativos, sinais, achados] = await Promise.all([
      api("/api/v1/resumo"), api("/api/v1/ativos"),
      api("/api/v1/sinais"), api("/api/v1/achados"),
    ]);
    Object.assign(estado, { resumo, ativos, sinais, achados });
    estado.fichas.clear();
    if (!estado.selecionado && ativos.length) {
      estado.selecionado = ativos.reduce(
        (a, b) => (b.dispositivos.length > a.dispositivos.length ? b : a), ativos[0]).ativo_id;
    }
    pintarTopo();
    pintarArvore();
    await pintarSecao();
    document.getElementById("rodape").textContent =
      `${resumo.ativos} ativos · ${resumo.dispositivos} dispositivos · ` +
      `${resumo.arestas_abertas} arestas abertas · atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
  }

  document.getElementById("arvore").addEventListener("click", (e) => {
    const bAtivo = e.target.closest("[data-ativo]");
    if (bAtivo) { estado.secao = "ativos"; marcarSecao(); abrirAtivo(bAtivo.dataset.ativo); return; }
    const bFrota = e.target.closest("[data-frota]");
    if (bFrota) {
      const f = bFrota.dataset.frota;
      estado.abertos.has(f) ? estado.abertos.delete(f) : estado.abertos.add(f);
      pintarArvore();
    }
  });

  function marcarSecao() {
    document.querySelectorAll(".secoes button").forEach((b) =>
      b.setAttribute("aria-current", String(b.dataset.secao === estado.secao)));
  }

  document.querySelector(".secoes").addEventListener("click", (e) => {
    const b = e.target.closest("[data-secao]");
    if (!b) return;
    estado.secao = b.dataset.secao;
    marcarSecao();
    pintarSecao();
  });

  let t;
  document.getElementById("busca").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { estado.filtro = e.target.value.trim(); pintarArvore(); }, 120);
  });

  atualizar().catch((erro) => {
    document.getElementById("centro").innerHTML =
      `<p class="aviso">Não consegui falar com a API: ${esc(erro.message)}</p>`;
  });
  setInterval(() => atualizar().catch(() => {}), 30000);
})();
