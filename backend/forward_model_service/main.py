import logging
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger("ramchems.forward_service")

# Try to import torch and transformers, but mock if unavailable
try:
    import torch
    from transformers import T5Tokenizer, T5ForConditionalGeneration
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    logger.warning("PyTorch/Transformers not available. Running in MOCK mode.")

app = FastAPI(title="Forward Reaction Prediction Microservice")

class ReactionRequest(BaseModel):
    reactants_smiles: List[str]
    top_k: int = 5

class Prediction(BaseModel):
    smiles: str
    confidence: float

class ReactionResponse(BaseModel):
    predicted_products: List[Prediction]
    model_name: str
    model_version: str
    inference_time_ms: int

MODEL_NAME = os.getenv("FORWARD_MODEL_ID", "sagawa/ReactionT5v2-forward-USPTO_MIT")

# Global model state
tokenizer = None
model = None

@app.on_event("startup")
def load_model():
    global tokenizer, model
    if not HAS_TORCH:
        return
    try:
        logger.info(f"Loading {MODEL_NAME}...")
        tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME)
        model = T5ForConditionalGeneration.from_pretrained(MODEL_NAME)
        model.eval()
        if torch.cuda.is_available():
            model = model.to("cuda")
        logger.info("Model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")

@app.post("/predict", response_model=ReactionResponse)
def predict(request: ReactionRequest):
    import time
    start = time.time()
    
    if not HAS_TORCH or model is None:
        # Mock behavior for testing architecture
        return ReactionResponse(
            predicted_products=[Prediction(smiles="CC(=O)Oc1ccccc1C(=O)O", confidence=0.99)],
            model_name="mock-t5",
            model_version="mock-v1",
            inference_time_ms=5
        )

    try:
        # T5v2 expects input as: "REACTANT:{reactants}REAGENT:{reagents}"
        # We assume reagents are empty since AiZynthFinder provides pure reactants in the step
        reactants_str = ".".join(request.reactants_smiles)
        input_text = f"REACTANT:{reactants_str}REAGENT:"
        
        device = next(model.parameters()).device
        inputs = tokenizer(input_text, return_tensors="pt").to(device)
        
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_length=128,
                num_return_sequences=request.top_k,
                num_beams=max(10, request.top_k),
                output_scores=True,
                return_dict_in_generate=True
            )
            
        sequences = outputs.sequences
        # Approx confidence from sequence scores
        scores = outputs.sequences_scores if hasattr(outputs, "sequences_scores") else [1.0] * len(sequences)
        
        predictions = []
        for seq, score in zip(sequences, scores):
            pred_smiles = tokenizer.decode(seq, skip_special_tokens=True)
            # Normalise probability (roughly)
            conf = torch.exp(score).item() if isinstance(score, torch.Tensor) else score
            predictions.append(Prediction(smiles=pred_smiles, confidence=conf))
            
        return ReactionResponse(
            predicted_products=predictions,
            model_name=MODEL_NAME,
            model_version="transformers-v1",
            inference_time_ms=int((time.time() - start) * 1000)
        )
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
