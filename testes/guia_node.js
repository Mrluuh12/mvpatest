/*
 * Roda o bloco GUIA de ptx/interface.html fora do navegador.
 *
 *   node testes/guia_node.js <casos.json>
 *
 * Cada caso diz qual funcao chamar e com que argumentos; a resposta sai em
 * JSON, na mesma ordem. O teste em python monta a geometria e confere.
 */
var fs = require('fs'), path = require('path');
var html = fs.readFileSync(path.join(path.dirname(__dirname), 'ptx', 'interface.html'), 'utf8');
var a = html.indexOf('/* === INICIO GUIA'), b = html.indexOf('/* === FIM GUIA');
if (a < 0 || b < 0) { console.error('nao achei o bloco GUIA'); process.exit(2); }
var mod = { exports: {} };
var Guia = new Function('module', 'exports', html.substring(a, b) + '\nreturn module.exports;')(mod, mod.exports);

var casos = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
var saida = casos.map(function (c) {
  if (c.f === 'manobras') return Guia.manobras(c.rota, Guia.juncoes(c.malha));
  if (c.f === 'grau')     return Guia.grau(Guia.juncoes(c.malha), c.ponto);
  if (c.f === 'progresso') return Guia.progresso(c.rota, c.lon, c.lat);
  if (c.f === 'proxima')  return Guia.proxima(c.lista, c.ao_longo);
  if (c.f === 'frase')    return Guia.frase(c.man, c.dist);
  return { erro: 'funcao desconhecida: ' + c.f };
});
process.stdout.write(JSON.stringify(saida));
