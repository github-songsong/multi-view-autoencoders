import torch
import hydra

from ..base.constants import MODEL_MVAE
from ..base.base_model import BaseModelVAE
from ..base.distributions import Normal
from ..base.exceptions import ModelInputError
from ..base.representations import ProductOfExperts, MeanRepresentation

class mVAE(BaseModelVAE):
    """
    Multi-view Variational Autoencoder model with a joint latent representation.

    Latent representations are joined either using the Product of Experts (https://arxiv.org/pdf/1410.7827.pdf)
    or the mean of the representations.

    Option to impose sparsity on the latent representations using a Sparse Multi-Channel Variational Autoencoder (http://proceedings.mlr.press/v97/antelmi19a.html)

    """

    def __init__(
        self,
        cfg = None,
        input_dim = None,
        z_dim = None
    ):
        super().__init__(model_name=MODEL_MVAE,
                        cfg=cfg,
                        input_dim=input_dim,
                        z_dim=z_dim)

        self.join_type = self.cfg.model.join_type
        if self.join_type == "PoE":
            self.join_z = ProductOfExperts()
        elif self.join_type == "Mean":
            self.join_z = MeanRepresentation()
        else:
            raise ModelInputError(f"[MVAE] Incorrect join method: {self.join_type}")

    def encode(self, x):
        mu = []
        var = []
        for i in range(self.n_views):
            mu_, logvar_ = self.encoders[i](x[i])
            mu.append(mu_)
            var_ = logvar_.exp()
            var.append(var_)
        mu = torch.stack(mu)
        var = torch.stack(var)
        mu_out, var_out = self.join_z(mu, var)
        qz_x = hydra.utils.instantiate(
            self.cfg.encoder.enc_dist, loc=mu_out, scale=var_out.pow(0.5)
        )
        return [qz_x]

    def decode(self, qz_x):
        px_zs = []
        for i in range(self.n_views):
            px_z = self.decoders[i](qz_x[0]._sample(training=self._training))
            px_zs.append(px_z)
        return px_zs

    def forward(self, x):
        qz_x = self.encode(x)
        px_zs = self.decode(qz_x)
        fwd_rtn = {"px_zs": px_zs, "qz_x": qz_x}
        return fwd_rtn

    def calc_kl(self, qz_x):
        """
        VAE: Implementation from: https://arxiv.org/abs/1312.6114
        sparse-VAE: Implementation from: https://github.com/senya-ashukha/variational-dropout-sparsifies-dnn/blob/master/KL%20approximation.ipynb
        """
        if self.sparse:
            kl = qz_x[0].sparse_kl_divergence().sum(1, keepdims=True).mean(0)
        else:
            kl = qz_x[0].kl_divergence(self.prior).sum(1, keepdims=True).mean(0)
        return self.beta * kl

    def calc_ll(self, x, px_zs):
        ll = 0
        for i in range(self.n_views):
            ll += px_zs[i][0].log_likelihood(x[i]).sum(1, keepdims=True).mean(0)
        return ll

    def loss_function(self, x, fwd_rtn):
        px_zs = fwd_rtn["px_zs"]
        qz_x = fwd_rtn["qz_x"]

        kl = self.calc_kl(qz_x)
        ll = self.calc_ll(x, px_zs)

        total = kl - ll
        losses = {"loss": total, "kl": kl, "ll": ll}
        return losses
