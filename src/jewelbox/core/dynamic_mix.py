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

# Pistes gardées derrière la piste en cours (« précédent » y revient) avant
# d'être consommées. Parité MIX_KEEP_BEHIND de la PWA et d'Android.
KEEP_BEHIND = 4


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


def trim_ids(played_before) -> tuple[int, ...]:
    """Pistes à consommer quand on avance à la main dans le mix.

    Une piste passée n'est pas consommée (seule une écoute complète l'est) :
    sans ça, sauter de piste en piste épuiserait la file sans que le serveur
    la recomplète. Arrivé à la piste KEEP_BEHIND + 1, celles d'avant sont
    donc consommées : la piste en cours repasse en tête et le bas de la liste
    se recomplète. played_before : les pistes avant la courante, dans
    l'ordre de lecture."""
    played_before = tuple(played_before)
    return played_before if len(played_before) >= KEEP_BEHIND else ()
