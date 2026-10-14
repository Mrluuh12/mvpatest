@echo off
REM =====================================================================
REM  instalar_servico.bat - poe o servidor de rotas para rodar sozinho,
REM  em segundo plano, sem janela.
REM
REM  Cria uma tarefa agendada que sobe no arranque do Windows, como SYSTEM.
REM  Sem janela nenhuma: ninguem fecha o servidor sem querer, e clicar
REM  dentro do console nao congela mais o processo. So' um administrador
REM  consegue parar. Se o servidor cair, o --vigia o levanta de novo em
REM  segundos.
REM
REM  USO (como Administrador):
REM    instalar_servico.bat                      (usa o config.json ao lado)
REM    instalar_servico.bat "C:\Modular\Modular.db" 5000
REM    instalar_servico.bat --parar      para agora (volta no proximo arranque)
REM    instalar_servico.bat --iniciar    sobe agora
REM    instalar_servico.bat --remover    tira de vez
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

if /i "%~1"=="--parar" goto parar
if /i "%~1"=="--iniciar" goto iniciar
if /i "%~1"=="--remover" goto remover

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

REM Os argumentos sao montados em linhas soltas, fora de blocos ( ): dentro
REM de um bloco o %ARGS% e' lido antes do set e o que veio antes se perde.
REM --vigia: levanta o servidor de novo se ele cair.
REM --sem-console: tarefa em segundo plano nao tem console para escrever.
set ARGS=--vigia --sem-console
if exist "%~dp0config.json" goto com_config
if not exist "%BANCO%" echo AVISO: o banco %BANCO% nao existe agora; o servidor nao sobe sem ele.
set ARGS=%ARGS% --db "%BANCO%" --porta %PORTA%
if exist "%~dp0rajant.json" set ARGS=%ARGS% --rajant-config "%~dp0rajant.json"
if exist "%~dp0rajant.json" echo Frota Rajant ligada por rajant.json
goto log

:com_config
echo Configuracao vinda de config.json (banco, porta, frota, fotos)

:log
REM Sem janela, o log e' o unico lugar onde se ve o que aconteceu. Se o
REM config.json ja' diz onde gravar, vale o dele.
findstr /c:"\"log\"" "%~dp0config.json" >nul 2>&1
if errorlevel 1 set ARGS=%ARGS% --log "%~dp0servidor.log"

REM dentro do /TR as aspas dos caminhos vao como \"
set "TR=%ARGS:"=\"%"

echo.
echo === instalando a tarefa "%TAREFA%" ===
echo   executavel: %EXE%
echo   argumentos: %ARGS%

REM se ja' existe uma rodando, para antes de trocar
schtasks /End /TN "%TAREFA%" >nul 2>&1
taskkill /F /T /IM servidor_rotas.exe >nul 2>&1

schtasks /Create /TN "%TAREFA%" /TR "\"%EXE%\" %TR%" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if errorlevel 1 (
  echo ERRO ao criar a tarefa.
  exit /b 1
)

REM Tarefa criada pelo schtasks vem com tres armadilhas: e' morta depois de
REM 3 dias rodando, para se o computador for para a bateria e nao reinicia
REM se falhar. Para um servidor que fica ligado meses, as tres derrubam.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName '%TAREFA%' -Settings $s | Out-Null"
if errorlevel 1 (
  echo.
  echo AVISO: nao consegui ajustar a tarefa pelo PowerShell.
  echo   Abra o Agendador de Tarefas, tarefa %TAREFA%, aba Configuracoes, e
  echo   DESMARQUE "Interromper a tarefa se ela for executada por mais de 3 dias".
  echo   Sem isso o servidor para sozinho a cada 3 dias.
)

schtasks /Run /TN "%TAREFA%"

echo.
echo === pronto: o servidor roda em segundo plano, sem janela ===
echo   sobe sozinho quando o Windows liga, e volta sozinho se cair.
echo.
echo   esta' no ar?  http://localhost:%PORTA%/api/saude
echo   mapa         http://localhost:%PORTA%/
echo   log          %~dp0servidor.log
echo   parar        instalar_servico.bat --parar
echo.
echo Nao abra mais o servidor_rotas.exe com dois cliques: a porta ja' esta'
echo ocupada pelo que roda em segundo plano.
echo.
echo Libere a porta %PORTA% no firewall para os PTX alcancarem o servidor:
echo   netsh advfirewall firewall add rule name="Servidor de rotas da mina" ^
dir=in action=allow protocol=TCP localport=%PORTA%
echo.
endlocal
exit /b 0

:parar
REM o --vigia levantaria o servidor de novo: para a tarefa e os dois processos
schtasks /End /TN "%TAREFA%"
taskkill /F /T /IM servidor_rotas.exe >nul 2>&1
echo Servidor parado. Volta no proximo arranque do Windows,
echo ou agora com:  instalar_servico.bat --iniciar
exit /b 0

:iniciar
schtasks /Run /TN "%TAREFA%"
exit /b %errorlevel%

:remover
schtasks /End /TN "%TAREFA%" >nul 2>&1
taskkill /F /T /IM servidor_rotas.exe >nul 2>&1
schtasks /Delete /TN "%TAREFA%" /F
echo Tarefa removida. O servidor nao sobe mais sozinho.
exit /b 0
