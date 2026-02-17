import os, numpy as np, torch, clip, cv2

root = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
mix = os.path.join(root, "data", "tg", "raw", "MIX")
out_path = os.path.join(root, "out", "embeddings.npy")
os.makedirs(os.path.dirname(out_path), exist_ok=True)

device = "cuda" if torch.cuda.is_available() else "cpu"
model, preprocess = clip.load("ViT-B/32", device=device)

def get_frame(video_path):
    cap = cv2.VideoCapture(video_path)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame

emb = {}
count = 0

for r,_,fs in os.walk(mix):
    for f in fs:
        if not f.lower().endswith(".mp4"):
            continue
        path = os.path.join(r,f)
        frame = get_frame(path)
        if frame is None:
            continue

        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = preprocess(torch.from_numpy(image).permute(2,0,1).float()/255.0).unsqueeze(0).to(device)

        with torch.no_grad():
            vec = model.encode_image(image).cpu().numpy()[0]

        emb[path] = vec
        count += 1
        if count % 50 == 0:
            print("encoded:", count)

np.save(out_path, emb)
print("DONE embeddings:", len(emb), "->", out_path)
