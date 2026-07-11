# Régénère demo/download_album.png
#
# EXIGE : un compte configuré (ARL Deezer dans config.toml) + réseau.
# La capture PNG est prise PENDANT le téléchargement (barres de progression
# Rich actives), via Screenshot — pas à la fin. vhs impose une sortie
# principale : ici un .gif (sous-produit animé du téléchargement).
# Usage :
#   vhs demo/download_album.tape
#   rm demo/download_album.gif      # sous-produit, à supprimer (ou à garder)
#
# Remplace l'URL par un album réel de ton choix.

Output demo/download_album.gif

Set Shell bash
Set FontSize 22
Set Width 1618
Set Height 1088
Set Padding 20

Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip url 'https://www.deezer.com/album/302127'"
Enter
# Laisse la résolution + les premières barres de progression apparaître.
Sleep 6s
Screenshot demo/download_album.png
# Prolonge un peu pour que le .gif montre la progression.
Sleep 5s
