/*
 * Harness de teste do nucleo de rota da interface.
 *
 * Extrai o bloco "NUCLEO ROTA" de ptx/interface.html, roda ele no node e
 * responde os casos que o teste em python mandou. Serve para provar que a
 * rota calculada no terminal (quando o servidor esta' fora do ar) bate com a
 * rota calculada pelo servidor.
 *
 *   node testes/nucleo_rota_node.js <malha.json> <locais.json> <casos.json>
 */
var fs = require('fs'), path = require('path');

var raiz = path.dirname(__dirname);
var html = fs.readFileSync(path.join(raiz, 'ptx', 'interface.html'), 'utf8');
var a = html.indexOf('/* === INICIO NUCLEO ROTA');
var b = html.indexOf('/* === FIM NUCLEO ROTA');
if (a < 0 || b < 0) {
  console.error('nao achei o bloco NUCLEO ROTA em ptx/interface.html');
  process.exit(2);
}
var mod = { exports: {} };
var NucleoRota = new Function('module', 'exports',
  html.substring(a, b) + '\nreturn module.exports;')(mod, mod.exports);

var malha  = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
var locais = JSON.parse(fs.readFileSync(process.argv[3], 'utf8')).locais;
var casos  = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));

var nucleo = new NucleoRota().carregar(malha);
var porNome = {};
for (var i = 0; i < locais.length; i++) porNome[locais[i].nome] = locais[i];

var saida = { nos: Object.keys(nucleo.nos).length,
              trechos: nucleo.trechos.length, respostas: [] };
for (i = 0; i < casos.length; i++) {
  var c = casos[i], L = porNome[c.destino];
  var r = nucleo.rota(c.lat, c.lon, c.destino,
                      L ? { lat: L.lat, lon: L.lon } : null);
  saida.respostas.push({
    ok: !!r.ok,
    distancia_m: r.ok ? r.distancia_m : null,
    n_nos: r.ok ? r.n_nos : null,
    pontos: r.ok ? r.geometria.coordinates.length : 0,
    erro: r.ok ? null : r.erro
  });
}
process.stdout.write(JSON.stringify(saida));
