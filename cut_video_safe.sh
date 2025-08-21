#!/bin/bash

# Script pour couper une partie du milieu d'une vidéo avec réencodage
# Usage: ./cut_video_safe.sh input.mp4 00:00:30 00:01:45 output.mp4

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

echo "📹 Traitement de la vidéo (avec réencodage pour compatibilité)..."
echo "Suppression de $CUT_START à $CUT_END"

# Méthode 1: Utiliser filter_complex (plus fiable pour .mov)
ffmpeg -i "$INPUT" -filter_complex "\
[0:v]trim=end=$CUT_START,setpts=PTS-STARTPTS[v1]; \
[0:a]atrim=end=$CUT_START,asetpts=PTS-STARTPTS[a1]; \
[0:v]trim=start=$CUT_END,setpts=PTS-STARTPTS[v2]; \
[0:a]atrim=start=$CUT_END,asetpts=PTS-STARTPTS[a2]; \
[v1][a1][v2][a2]concat=n=2:v=1:a=1[outv][outa]" \
-map "[outv]" -map "[outa]" \
-c:v libx264 -preset fast -crf 22 \
-c:a aac -b:a 192k \
-y "$OUTPUT"

if [ $? -eq 0 ]; then
    echo "✅ Vidéo créée avec succès : $OUTPUT"
    
    # Afficher les infos de la vidéo
    duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUTPUT" 2>/dev/null)
    if [ ! -z "$duration" ]; then
        minutes=$(echo "$duration / 60" | bc)
        seconds=$(echo "$duration - ($minutes * 60)" | bc)
        echo "📏 Durée finale : ${minutes}m ${seconds}s"
    fi
    
    # Afficher la taille du fichier
    size=$(ls -lh "$OUTPUT" | awk '{print $5}')
    echo "📦 Taille du fichier : $size"
else
    echo "❌ Erreur lors du traitement de la vidéo"
fi