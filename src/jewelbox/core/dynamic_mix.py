"""Synchronisation du mix dynamique : logique pure, sans GStreamer ni réseau.

Miroir de DynamicMixSync.kt côté Android. Le serveur ne fait jamais que
retirer les pistes consommées (ou devenues injouables) et recompléter le bas
de la liste : remettre la file locale à jour revient donc exactement à
retirer ce qui a disparu et ajouter à la fin ce qui est nouveau — l'ordre
relatif des pistes restantes ne change jamais.
"""

from dataclasses import dataclass

# Clé de la liste intelligente « Mix dynamique » côté serveur.
DYNAMIC_MIX_KEY = 'dynamic_mix'


@dataclass(frozen=True)
class MixSyncPlan:
    # Pistes présentes localement mais absentes de la liste du serveur.
    remove_ids: tuple[int, ...] = ()
    # Pistes du serveur absentes localement, à ajouter en fin de file, dans
    # l'ordre du serveur.
    append_ids: tuple[int, ...] = ()


def plan(current_ids, server_ids) -> MixSyncPlan:
    server = set(server_ids)
    local = set(current_ids)
    return MixSyncPlan(
        remove_ids=tuple(i for i in current_ids if i not in server),
        append_ids=tuple(i for i in server_ids if i not in local),
    )
