"""Aplicação da plataforma: repositório e API de leitura sobre o inventário."""

from .repositorio import AtivoLido, DispositivoLido, Repositorio, RepositorioMemoria

__all__ = ["AtivoLido", "DispositivoLido", "Repositorio", "RepositorioMemoria"]
