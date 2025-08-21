#!/bin/bash

# Script pour couper une partie du milieu d'une vidéo
# Usage: ./cut_video.sh input.mp4 00:00:30 00:01:45 output.mp4

if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <input_video> <start_cut> <end_cut> <output_video>"
    echo "Exemple: $0 video.mp4 00:00:30 00:01:45 video_courte.mp4"
    echo "Cela supprimera la partie entre 30s et 1m45s"
    exit 1
fi

INPUT="$1"
CUT_START="$2"
CUT_END="$3"
OUTPUT="$4"

# Convertir les timecodes en secondes
start_seconds=$(echo "$CUT_START" | awk -F: '{ print ($1 * 3600) + ($2 * 60) + $3 }')
end_seconds=$(echo "$CUT_END" | awk -F: '{ print ($1 * 3600) + ($2 * 60) + $3 }')

echo "📹 Traitement de la vidéo..."
echo "Suppression de $CUT_START à $CUT_END"

# Extraire la première partie (avant la coupe)
ffmpeg -i "$INPUT" -to "$CUT_START" -c copy -y part1.mp4 2>/dev/null

# Extraire la deuxième partie (après la coupe)
ffmpeg -i "$INPUT" -ss "$CUT_END" -c copy -y part2.mp4 2>/dev/null

# Créer le fichier de concaténation
echo "file 'part1.mp4'" > concat_list.txt
echo "file 'part2.mp4'" >> concat_list.txt

# Fusionner les parties
ffmpeg -f concat -safe 0 -i concat_list.txt -c copy -y "$OUTPUT" 2>/dev/null

# Nettoyer les fichiers temporaires
rm -f part1.mp4 part2.mp4 concat_list.txt

echo "✅ Vidéo créée : $OUTPUT"

# Afficher la durée de la nouvelle vidéo
duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUTPUT" 2>/dev/null)
echo "📏 Durée finale : $(printf '%02d:%02d' $(echo "$duration/60" | bc) $(echo "$duration%60" | bc)) ($(echo "scale=1; $duration" | bc)s)"
