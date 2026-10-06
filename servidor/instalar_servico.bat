@echo off
REM =====================================================================
REM  instalar_servico.bat - poe o servidor de rotas para subir sozinho.
REM
REM  Cria uma tarefa agendada que roda no arranque do Windows, como
REM  SYSTEM, e reinicia sozinha se cair.
REM
REM  USO (como Administrador):
REM    instalar_servico.bat                      (usa o config.json ao lado)
REM    instalar_servico.bat "C:\Modular\Modular.db" 5000
REM    instalar_servico.bat --remover
REM
REM  Para a frota Rajant, preencha rajant.json ao lado do executavel:
REM    {"cache":"rajant_ips_cache.json","senha":"SENHA_DO_RADIO","intervalo":10}
REM =====================================================================
setlocal
cd /d "%~dp0"

set TAREFA=ServidorRotasMina

net session >nul 2>&1
if errorlevel 1 (
  echo ERRO: rode este arquivo como Administrador.
  echo   clique com o botao direito ^> Executar como administrador
  exit /b 1
)

if /i "%~1"=="--remover" (
  schtasks /Delete /TN "%TAREFA%" /F
  echo Tarefa removida. O servidor nao sobe mais sozinho.
  exit /b 0
)

set BANCO=%~1
if "%BANCO%"=="" set BANCO=C:\Modular\Modular.db
set PORTA=%~2
if "%PORTA%"=="" set PORTA=5000

set EXE=%~dp0dist\servidor_rotas.exe
if not exist "%EXE%" set EXE=%~dp0servidor_rotas.exe
if not exist "%EXE%" (
  echo ERRO: nao achei o servidor_rotas.exe.
  echo Rode o gerar_exe.bat antes.
  exit /b 1
)

if not exist "%BANCO%" (
  echo AVISO: o banco %BANCO% nao existe agora.
  echo Confira o caminho; o servidor nao sobe sem ele.
)

REM Com config.json ao lado do executavel, ele decide tudo: banco, porta,
REM frota e atualizacao. A tarefa entao roda sem argumento nenhum.
REM --sem-console porque tarefa agendada nao tem console para escrever.
if exist "%~dp0config.json" (
  set ARGS=--sem-console
  echo Configuracao vinda de config.json
) else (
  set ARGS=--db "%BANCO%" --porta %PORTA% --log "%~dp0servidor.log" --sem-console
  if exist "%~dp0rajant.json" (
    set ARGS=%ARGS% --rajant-config "%~dp0rajant.json"
    echo Frota Rajant ligada por rajant.json
  )
)

echo.
echo === instalando a tarefa "%TAREFA%" ===
echo   executavel: %EXE%
echo   banco     : %BANCO%
echo   porta     : %PORTA%

schtasks /Create /TN "%TAREFA%" /TR "\"%EXE%\" %ARGS%" /SC ONSTART ^
  /RU SYSTEM /RL HIGHEST /F
if errorlevel 1 (
  echo ERRO ao criar a tarefa.
  exit /b 1
)

schtasks /Run /TN "%TAREFA%"

echo.
echo === confira ===
echo   estado : schtasks /Query /TN "%TAREFA%"
echo   log    : %~dp0servidor.log
echo   frota  : http://localhost:%PORTA%/api/saude  (leituras tem que subir)
echo   API    : http://localhost:%PORTA%/api/saude
echo   mapa   : http://localhost:%PORTA%/
echo.
echo Libere a porta %PORTA% no firewall para os PTX alcancarem o servidor:
echo   netsh advfirewall firewall add rule name="Servidor de rotas da mina" ^
dir=in action=allow protocol=TCP localport=%PORTA%
echo.
endlocal
