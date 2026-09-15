"""Forward reaction validation interface and data models."""
from __future__ import annotations

import enum
import itertools
import logging
import time
from typing import Any, Optional

from rdkit import Chem
from rdkit.Chem import AllChem

logger = logging.getLogger("ramchems.validation")


class ValidationStatus(str, enum.Enum):
    MATCH = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    MISMATCH = "MISMATCH"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class ForwardValidationResult:
    def __init__(
        self,
        status: ValidationStatus,
        predicted_product: Optional[str] = None,
        target_product: Optional[str] = None,
        model_name: Optional[str] = None,
        model_version: Optional[str] = None,
        inference_time_ms: Optional[int] = None,
        error: Optional[str] = None,
        interpretation: Optional[str] = None,
    ):
        self.status = status
        self.predicted_product = predicted_product
        self.target_product = target_product
        self.model_name = model_name
        self.model_version = model_version
        self.inference_time_ms = inference_time_ms
        self.error = error
        self.interpretation = interpretation

    def to_dict(self) -> dict[str, Any]:
        d = {
            "status": self.status.value,
            "interpretation": self.interpretation,
        }
        if self.model_name and self.model_version:
            d["model"] = f"{self.model_name}-{self.model_version}"
        elif self.model_name:
            d["model"] = self.model_name
            
        if self.predicted_product:
            d["predicted_products"] = [self.predicted_product]
        else:
            d["predicted_products"] = []
            
        if self.inference_time_ms is not None:
            d["inference_time_ms"] = self.inference_time_ms
            
        if self.error:
            d["error"] = self.error
        return d


class ForwardModel:
    def validate_step(
        self, target_product_smiles: str, reactants_smiles: list[str], **kwargs
    ) -> ForwardValidationResult:
        """Validate a single reaction step."""
        raise NotImplementedError


class RDKitTemplateReversalModel(ForwardModel):
    """
    Candidate 1 fallback: Uses the AiZynthFinder SMARTS template in the forward
    direction via RDKit to structurally verify that applying the exact reaction
    rule to the chosen precursors actually yields the intended product.
    """

    def __init__(self):
        self.name = "rdkit-template-reversal"
        self.version = "2023.09.3"

    @staticmethod
    def _reverse_aizynth_template(template: str) -> str:
        """
        Reverses an AiZynthFinder template (Target >> Precursors)
        into a forward template (Precursors >> Target).
        """
        parts = template.split(">")
        if len(parts) == 3:
            target, agents, precursors = parts
            return f"{precursors}>{agents}>{target}"
        elif len(parts) == 2 and ">>" in template:
            target, _, precursors = parts
            return f"{precursors}>>{target}"
        elif ">>" in template:
            target, precursors = template.split(">>", 1)
            return f"{precursors}>>{target}"
        return template

    def validate_step(
        self, target_product_smiles: str, reactants_smiles: list[str], **kwargs
    ) -> ForwardValidationResult:
        start_t = time.time()
        
        template_smarts = kwargs.get("template_smarts")
        if not template_smarts:
            return ForwardValidationResult(
                status=ValidationStatus.VALIDATION_ERROR,
                error="No template_smarts provided for template reversal",
                model_name=self.name,
                model_version=self.version,
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="structural_consistency"
            )

        try:
            target_mol = Chem.MolFromSmiles(target_product_smiles)
            if not target_mol:
                raise ValueError("Invalid target SMILES")
            
            target_canonical = Chem.MolToSmiles(target_mol, isomericSmiles=True)
            target_canonical_no_stereo = Chem.MolToSmiles(target_mol, isomericSmiles=False)
            
            reactants = []
            for rsmi in reactants_smiles:
                rmol = Chem.MolFromSmiles(rsmi)
                if rmol:
                    reactants.append(rmol)
                else:
                    raise ValueError(f"Invalid reactant SMILES: {rsmi}")

            forward_template = self._reverse_aizynth_template(template_smarts)
            rxn = AllChem.ReactionFromSmarts(forward_template)
            if not rxn:
                raise ValueError("Invalid SMARTS template")

            predicted_products = []
            best_status = ValidationStatus.MISMATCH
            best_predicted = None

            # Try all permutations of the provided reactants matching the required number
            num_templates = rxn.GetNumReactantTemplates()
            all_outcomes = []
            
            for perm in itertools.permutations(reactants, num_templates):
                try:
                    outcomes = rxn.RunReactants(perm)
                    if outcomes:
                        all_outcomes.extend(outcomes)
                except ValueError:
                    pass

            if not all_outcomes:
                best_status = ValidationStatus.MISMATCH
            else:
                for outcome in all_outcomes:
                    for product_mol in outcome:
                        try:
                            Chem.SanitizeMol(product_mol)
                            psmi = Chem.MolToSmiles(product_mol, isomericSmiles=True)
                            predicted_products.append(psmi)
                            
                            if psmi == target_canonical:
                                best_status = ValidationStatus.MATCH
                                best_predicted = psmi
                                break
                            
                            psmi_no_stereo = Chem.MolToSmiles(product_mol, isomericSmiles=False)
                            if psmi_no_stereo == target_canonical_no_stereo:
                                if best_status != ValidationStatus.MATCH:
                                    best_status = ValidationStatus.PARTIAL_MATCH
                                    best_predicted = psmi
                        except Exception:
                            continue
                    if best_status == ValidationStatus.MATCH:
                        break
            
            if best_status == ValidationStatus.MISMATCH and predicted_products:
                best_predicted = predicted_products[0]

            return ForwardValidationResult(
                status=best_status,
                predicted_product=best_predicted,
                target_product=target_canonical,
                model_name=self.name,
                model_version=self.version,
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="structural_consistency"
            )

        except Exception as e:
            logger.warning(f"RDKit validation failed: {e}")
            return ForwardValidationResult(
                status=ValidationStatus.VALIDATION_ERROR,
                error=str(e),
                model_name=self.name,
                model_version=self.version,
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="structural_consistency"
            )


class MicroserviceLearnedForwardModel(ForwardModel):
    """
    Candidate 2: A learned sequence-to-sequence Molecular Transformer running in an 
    isolated PyTorch microservice. Provides completely independent regioselectivity 
    and overreaction checks.
    """

    def __init__(self, endpoint_url: str = "http://localhost:8435/predict"):
        self.endpoint_url = endpoint_url

    def validate_step(
        self, target_product_smiles: str, reactants_smiles: list[str], **kwargs
    ) -> ForwardValidationResult:
        import requests
        start_t = time.time()
        
        try:
            target_mol = Chem.MolFromSmiles(target_product_smiles)
            if not target_mol:
                raise ValueError("Invalid target SMILES")
            
            target_canonical = Chem.MolToSmiles(target_mol, isomericSmiles=True)
            target_canonical_no_stereo = Chem.MolToSmiles(target_mol, isomericSmiles=False)
            
            # Fire request to the microservice
            resp = requests.post(
                self.endpoint_url,
                json={"reactants_smiles": reactants_smiles, "top_k": 5},
                timeout=10.0
            )
            resp.raise_for_status()
            data = resp.json()
            
            predicted_products = data.get("predicted_products", [])
            model_name = data.get("model_name", "unknown")
            model_version = data.get("model_version", "unknown")
            
            best_status = ValidationStatus.MODEL_OUTPUT_INVALID
            best_predicted = None
            valid_predictions = False
            
            for pred in predicted_products:
                psmi_raw = pred.get("smiles", "")
                try:
                    pmol = Chem.MolFromSmiles(psmi_raw)
                    if pmol:
                        Chem.SanitizeMol(pmol)
                        psmi = Chem.MolToSmiles(pmol, isomericSmiles=True)
                        valid_predictions = True
                        
                        if psmi == target_canonical:
                            best_status = ValidationStatus.MATCH
                            best_predicted = psmi
                            break
                        
                        psmi_no_stereo = Chem.MolToSmiles(pmol, isomericSmiles=False)
                        if psmi_no_stereo == target_canonical_no_stereo:
                            if best_status not in (ValidationStatus.MATCH, ValidationStatus.PARTIAL_MATCH):
                                best_status = ValidationStatus.PARTIAL_MATCH
                                best_predicted = psmi
                except Exception:
                    continue
                    
            if valid_predictions and best_status == ValidationStatus.MODEL_OUTPUT_INVALID:
                best_status = ValidationStatus.MISMATCH

            if best_status in (ValidationStatus.MISMATCH, ValidationStatus.MODEL_OUTPUT_INVALID) and predicted_products:
                # Store top-1 prediction for mismatch visibility
                best_predicted = predicted_products[0].get("smiles", "")

            return ForwardValidationResult(
                status=best_status,
                predicted_product=best_predicted,
                target_product=target_canonical,
                model_name=model_name,
                model_version=model_version,
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="forward_model_agreement"
            )

        except requests.exceptions.RequestException as e:
            logger.warning(f"Learned forward model unavailable: {e}")
            return ForwardValidationResult(
                status=ValidationStatus.MODEL_UNAVAILABLE,
                error=f"Microservice error: {e}",
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="forward_model_agreement"
            )
        except Exception as e:
            logger.warning(f"Learned forward validation failed: {e}")
            return ForwardValidationResult(
                status=ValidationStatus.VALIDATION_ERROR,
                error=str(e),
                inference_time_ms=int((time.time() - start_t) * 1000),
                interpretation="forward_model_agreement"
            )
