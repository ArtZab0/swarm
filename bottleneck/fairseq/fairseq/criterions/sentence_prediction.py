# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import math
import numpy as np

import torch
import torch.nn.functional as F
from fairseq import metrics, utils
from fairseq.criterions import FairseqCriterion, register_criterion
from sklearn.metrics import matthews_corrcoef


@register_criterion("sentence_prediction")
class SentencePredictionCriterion(FairseqCriterion):
    def __init__(self, task, classification_head_name, regression_target):
        super().__init__(task)
        self.classification_head_name = classification_head_name
        self.regression_target = regression_target

    @staticmethod
    def add_args(parser):
        # fmt: off
        parser.add_argument('--classification-head-name',
                            default='sentence_classification_head',
                            help='name of the classification head to use')
        # fmt: on

    def forward(self, model, sample, reduce=True):
        """Compute the loss for the given sample.

        Returns a tuple with three elements:
        1) the loss
        2) the sample size, which is used as the denominator for the gradient
        3) logging outputs to display while training
        """
        assert (
            hasattr(model, "classification_heads")
            and self.classification_head_name in model.classification_heads
        ), "model must provide sentence classification head for --criterion=sentence_prediction"

        logits, _ = model(
            **sample["net_input"],
            features_only=True,
            classification_head_name=self.classification_head_name,
        )
        targets = model.get_targets(sample, [logits]).view(-1)
        sample_size = targets.numel()

        if not self.regression_target:
            lprobs = F.log_softmax(logits, dim=-1, dtype=torch.float32)
            loss = F.nll_loss(lprobs, targets, reduction="sum")
        else:
            logits = logits.view(-1).float()
            targets = targets.float()
            loss = F.mse_loss(logits, targets, reduction="sum")

        logging_output = {
            "loss": loss.data,
            "ntokens": sample["ntokens"],
            "nsentences": sample_size,
            "sample_size": sample_size,
        }
        if not self.regression_target:
            preds = logits.argmax(dim=1)
            lprobs = F.log_softmax(logits, dim=-1, dtype=torch.float32)
            logging_output['p'] = lprobs[torch.arange(logits.shape[0], device=logits.device), targets].detach().cpu().numpy()
            logging_output['ncorrect'] = (preds == targets).sum()
            logging_output['targets'] = targets.detach().cpu().numpy()
            logging_output['pred'] = preds.detach().cpu().numpy()
        else:
            logging_output['targets'] = targets.detach().cpu().numpy()
            logging_output['pred'] = logits.detach().cpu().numpy()

        return loss, sample_size, logging_output

    @staticmethod
    def reduce_metrics(logging_outputs) -> None:
        """Aggregate logging outputs from data parallel training."""
        loss_sum = sum(log.get("loss", 0) for log in logging_outputs)
        ntokens = sum(log.get("ntokens", 0) for log in logging_outputs)
        nsentences = sum(log.get("nsentences", 0) for log in logging_outputs)
        sample_size = sum(log.get("sample_size", 0) for log in logging_outputs)

        metrics.log_scalar(
            "loss", loss_sum / sample_size / math.log(2), sample_size, round=3
        )
        if sample_size != ntokens:
            metrics.log_scalar(
                "nll_loss", loss_sum / ntokens / math.log(2), ntokens, round=3
            )

        if len(logging_outputs) > 0 and 'p' in logging_outputs[0]:
            ncorrect = sum(log.get('ncorrect', 0) for log in logging_outputs)
            metrics.log_scalar('accuracy', 100.0 * ncorrect / nsentences, nsentences, round=1)
            pred = [log.get('pred') for log in logging_outputs]
            pred = np.concatenate(pred)
            t = [log.get('targets') for log in logging_outputs]
            t = np.concatenate(t)
            p = [log.get('p') for log in logging_outputs]
            p = np.concatenate(p)
            try:
                if pred.std() > 0:
                    metrics.log_scalar('corr', np.corrcoef(pred.flatten(), t.flatten())[1,0])
                    metrics.log_scalar('corrp', np.corrcoef(p.flatten(), t.flatten())[1,0])
                    metrics.log_scalar('mcorr', matthews_corrcoef(t.flatten(), pred.flatten()))
            except:
                pass
        else:
            metrics.log_scalar(
                "mse", loss_sum/sample_size , sample_size, round=3
            )

    @staticmethod
    def logging_outputs_can_be_summed() -> bool:
        """
        Whether the logging outputs returned by `forward` can be summed
        across workers prior to calling `reduce_metrics`. Setting this
        to True will improves distributed training speed.
        """
        return True