@echo off
REM =====================================================================
REM  gerar_exe.bat - constroi o servidor_rotas.exe (Windows).
REM
REM  Precisa de Python 3 instalado NESTA maquina. O PyInstaller nao faz
REM  compilacao cruzada: o .exe tem de ser gerado no Windows.
REM
REM  O executavel sai em dist\servidor_rotas.exe e roda sozinho, sem
REM  Python instalado na maquina de destino.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python 3 nao encontrado no PATH.
  echo Instale em https://www.python.org/downloads/ e marque "Add to PATH".
  exit /b 1
)

echo.
echo === instalando o que e preciso para construir ===
python -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1

echo.
echo === camada de frota Rajant (opcional) ===
echo Sem estes pacotes o servidor funciona igual, so sem a posicao dos
echo equipamentos lida do GPS dos radios.
python -m pip install --no-deps rajant-api
python -m pip install protobuf

echo.
echo === atualizacao do Modular.db pelos PTX (opcional) ===
echo O scp do Windows nao aceita senha na linha de comando; o paramiko sim.
python -m pip install paramiko

echo.
echo === construindo ===
python -m PyInstaller --onefile --clean --noconfirm ^
  --name servidor_rotas ^
  --paths . ^
  --hidden-import rajant_frota ^
  --hidden-import atualizar_db ^
  --hidden-import mapa_foto ^
  --hidden-import rajant_api ^
  --hidden-import google.protobuf ^
  --hidden-import paramiko ^
  --collect-submodules rajant_api ^
  --collect-submodules paramiko ^
  servidor_rotas.py
if errorlevel 1 (
  echo.
  echo ERRO na construcao. Se reclamou de modulo faltando, acrescente
  echo outro --hidden-import acima com o nome que ele pediu.
  exit /b 1
)

echo.
echo === pronto ===
echo   dist\servidor_rotas.exe
echo.
echo Leve para a pasta de producao:
echo   dist\servidor_rotas.exe
echo   config.json            (copie de config.exemplo.json e preencha)
echo   Modular.db             (ou aponte o caminho no config.json)
echo   rajant_ips_cache.json  (se for usar a frota)
echo.
echo Com o config.json ao lado, o servidor abre com DOIS CLIQUES.
echo Teste: dist\servidor_rotas.exe  e abra http://localhost:5000/
echo.
endlocal
