# PyTorch StudioGAN: https://github.com/POSTECH-CVLab/PyTorch-StudioGAN
# The MIT License (MIT)
# See license file or visit https://github.com/POSTECH-CVLab/PyTorch-StudioGAN for details

# src/utils/loss.py


from torch.nn import DataParallel
from torch import autograd
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

import utils.ops as ops


class ConditionalContrastive(torch.nn.Module):
    def __init__(self, device):
        super(ConditionalContrastive, self).__init__()
        self.device = device
        self.calculate_similarity_matrix = self._calculate_similarity_matrix()
        self.cosine_similarity = torch.nn.CosineSimilarity(dim=-1)

    def _calculate_similarity_matrix(self):
        return self._cosine_simililarity_matrix

    def remove_diag(self, M):
        h, w = M.shape
        assert h==w, "h and w should be same"
        mask = np.ones((h, w)) - np.eye(h)
        mask = torch.from_numpy(mask)
        mask = (mask).type(torch.bool).to(self.device)
        return M[mask].view(h, -1)

    def _cosine_simililarity_matrix(self, x, y):
        v = self.cosine_similarity(x.unsqueeze(1), y.unsqueeze(0))
        return v

    def forward(self, embed, proxy, mask, labels, temperature):
        sim_matrix = self.calculate_similarity_matrix(embed, embed)
        sim_matrix = torch.exp(self.remove_diag(sim_matrix)/temperature)
        neg_removal_mask = self.remove_diag(mask[labels])
        sim_btw_pos = neg_removal_mask*sim_matrix

        emb2proxy = torch.exp(self.cosine_similarity(embed, proxy)/temperature)

        numerator = emb2proxy + sim_btw_pos.sum(dim=1)
        denomerator = torch.cat([torch.unsqueeze(emb2proxy, dim=1), sim_matrix], dim=1).sum(dim=1)
        criterion = -torch.log(numerator/denomerator)
        return criterion.mean()

def d_vanilla(d_logit_real, d_logit_fake):
    device = d_logit_real.get_device()
    ones = torch.ones_like(d_logit_real, device=device, requires_grad=False)
    d_loss = -torch.mean(nn.LogSigmoid()(d_logit_real) + nn.LogSigmoid()(ones - d_logit_fake))
    return d_loss

def g_vanilla(g_logit_fake):
    return -torch.mean(nn.LogSigmoid()(g_logit_fake))

def d_ls(d_logit_real, d_logit_fake):
    d_loss = 0.5*(d_logit_real - torch.ones_like(d_logit_real))**2 + 0.5*(d_logit_fake)**2
    return d_loss.mean()

def g_ls(d_logit_fake):
    gen_loss = 0.5*(d_logit_fake - torch.ones_like(d_logit_fake))**2
    return gen_loss.mean()

def d_hinge(d_logit_real, d_logit_fake):
    return torch.mean(F.relu(1. - d_logit_real)) + torch.mean(F.relu(1. + d_logit_fake))

def g_hinge(g_logit_fake):
    return -torch.mean(g_logit_fake)

def d_wasserstein(d_logit_real, d_logit_fake):
    return torch.mean(d_logit_fake - d_logit_real)

def g_wasserstein(g_logit_fake):
    return -torch.mean(g_logit_fake)

def cal_deriv(inputs, outputs, device):
    grads = autograd.grad(outputs=outputs,
                          inputs=inputs,
                          grad_outputs=torch.ones(outputs.size()).to(device),
                          create_graph=True,
                          retain_graph=True,
                          only_inputs=True)[0]
    return grads

def latent_optimise(zs, fake_labels, generator, discriminator, batch_size, lo_rate, lo_steps, lo_alpha,
                    lo_beta, cal_trsf_cost, device):
    for step in range(lo_steps):
        drop_mask = (torch.FloatTensor(batch_size, 1).uniform_() > 1 - lo_rate).to(device)

        zs = autograd.Variable(zs, requires_grad=True)
        fake_images = generator(zs, fake_labels)
        output_dict = discriminator(fake_images, fake_labels, eval=False)
        z_grads = cal_deriv(inputs=zs, outputs=output_dict["adv_output"], device=device)
        z_grads_norm = torch.unsqueeze((z_grads.norm(2, dim=1)**2), dim=1)
        delta_z = lo_alpha*z_grads/(lo_beta + z_grads_norm)
        zs = torch.clamp(zs + drop_mask*delta_z, -1.0, 1.0)

        if cal_trsf_cost:
            if step == 0:
                trsf_cost = (delta_z.norm(2, dim=1)**2).mean()
            else:
                trsf_cost += (delta_z.norm(2, dim=1)**2).mean()
        else:
            trsf_cost = None
        return zs, trsf_cost

def cal_deriv4gp(real_images, real_labels, fake_images, discriminator, device):
    batch_size, c, h, w = real_images.shape
    alpha = torch.rand(batch_size, 1)
    alpha = alpha.expand(batch_size, real_images.nelement()//batch_size).contiguous().view(batch_size, c, h, w)
    alpha = alpha.to(device)

    real_images = real_images.to(device)
    interpolates = alpha*real_images + ((1 - alpha)*fake_images)
    interpolates = interpolates.to(device)
    interpolates = autograd.Variable(interpolates, requires_grad=True)
    output_dict = discriminator(interpolates, real_labels, eval=False)
    grads = cal_deriv(inputs=interpolates, outputs=output_dict["adv_output"], device=device)
    grads = grads.view(grads.size(0), -1)

    grads_penalty = ((grads.norm(2, dim=1) - 1)**2).mean()
    return grads_penalty

def calc_derv4dra(real_images, real_labels, discriminator, device):
    batch_size, c, h, w = real_images.shape
    alpha = torch.rand(batch_size, 1, 1, 1)
    alpha = alpha.to(device)

    real_images = real_images.to(device)
    differences  = 0.5*real_images.std()*torch.rand(real_images.size()).to(device)
    interpolates = real_images + (alpha*differences)
    interpolates = interpolates.to(device)
    interpolates = autograd.Variable(interpolates, requires_grad=True)
    output_dict = discriminator(interpolates, real_labels, eval=False)
    grads = cal_deriv(inputs=interpolates, outputs=output_dict["adv_output"], device=device)
    grads = grads.view(grads.size(0), -1)

    grads_penalty = ((grads.norm(2, dim=1) - 1)**2).mean()
    return grads_penalty
