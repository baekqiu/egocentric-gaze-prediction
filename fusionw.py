import torch
from torch.autograd import Variable
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader
from scipy import ndimage
from skimage import io
import math
from tqdm import tqdm
import cv2, os

from floss import floss
from data.STdatas import STTrainData, STValData
from utils import *
from models.LSTMnet import lstmnet
from models.SP import VGG_st_3dfuse
from models.late_fusion import late_fusion
import argparse

print('importing done!')
parser = argparse.ArgumentParser()
parser.add_argument('--lr', type=float, default=1e-7, required=False, help='lr for Adam')
parser.add_argument('--late_save_img', default='loss_late.png', required=False)
parser.add_argument('--pretrained_model', default='../savefusion/00004_fusion3d_bn_floss_checkpoint.pth.tar', required=False)
parser.add_argument('--pretrained_lstm', default='../savelstm/best_lstmnet.pth.tar', required=False)
parser.add_argument('--pretrained_late', default='../savelate/best.pth.tar', required=False)
parser.add_argument('--lstm_save_img', default='loss_lstm.png', required=False)
parser.add_argument('--save_lstm', default='best_lstm.pth.tar', required=False)
parser.add_argument('--save_late', default='best_late.pth.tar', required=False)
parser.add_argument('--save_path', default='../savelate', required=False)
parser.add_argument('--loss_function', default='f', required=False)
parser.add_argument('--num_epoch', type=int, default=10, required=False)
parser.add_argument('--train_lstm', type=bool, default=False, required=False)
parser.add_argument('--train_late', type=bool, default=False, required=False)
parser.add_argument('--extract_late', type=bool, default=False, required=False)
parser.add_argument('--extract_late_pred_folder', default='../new_pred/', required=False)
parser.add_argument('--extract_late_feat_folder', default='../new_feat/', required=False)
parser.add_argument('--device', default='0')
parser.add_argument('--batch_size', type=int, default=10)
parser.add_argument('--crop_size', type=int, default=3)
args = parser.parse_args()

device = torch.device('cuda:'+args.device)

batch_size = args.batch_size
hook_name = 'features_s'

global features_blobs
features_blobs = []
def hook_feature(module, input, output):
    features_blobs.append(output)

def crop_feature(feature, maxind, size):
    #maxind is gaze point
    H = feature.size(2)
    W = feature.size(3)
    for b in range(feature.size(0)):
        fmax = np.array(maxind[b])
        fmax = fmax / 16  #downsize from 224 to 14
        fmax = np.clip(fmax, size/2, H-int(math.ceil(size/2.0)))
        cfeature = feature[b,:,(fmax[0]-size/2):(fmax[0]+int(math.ceil(size/2.0))),(fmax[1]-size/2):(fmax[1]+int(math.ceil(size/2.0)))]
        cfeature = cfeature.unsqueeze(0)
        if b==0:
            res = cfeature
        else:
            res = torch.cat((res, cfeature),0)
    return res

def crop_feature_var(feature, maxind, size):
    H = feature.size(2)
    W = feature.size(3)
    for b in range(feature.size(0)):
        ind = maxind[b].item()
        fmax = np.unravel_index(ind, (H,W))
        fmax = np.clip(fmax, size/2, H-int(math.ceil(size/2.0)))
        cfeature = feature[b,:,(fmax[0]-size/2):(fmax[0]+int(math.ceil(size/2.0))),(fmax[1]-size/2):(fmax[1]+int(math.ceil(size/2.0)))]
        cfeature = cfeature.unsqueeze(0)
        if b==0:
            res = cfeature
        else:
            res = torch.cat((res, cfeature),0)
    return res


def visw(st_loader):
    from matplotlib import pyplot as plt
    for i,sample in enumerate(st_loader):
        inp = sample['input'].squeeze()   #(10, 513)
        target = sample['gt'].squeeze()    #(10, 512)
        inp = inp[0,:].cpu().numpy()
        target = target[0,:].cpu().numpy()
        plt.plot(inp)
        plt.show()
        plt.plot(target)
        plt.show()


def trainw(epoch, st_loader, modelw, criterion, optimizer):
    losses = AverageMeter()
    modelw.train()
    hidden = None
    feature_fusion = torch.ones(batch_size,512,1,1).to(device)
    currname = None
    tanh = nn.Tanh()
    relu = nn.ReLU()
    pred_chn_weight = None
    for i, sample in enumerate(st_loader):
        #reset hidden state only when a video is over
        same = sample['same']
        if int(same) == 0:
            hidden = None

        inp = sample['input'].unsqueeze(0)   #(1, 1, 512)
        target = sample['gt'].unsqueeze(0)    #(1,1, 512)

        if pred_chn_weight is not None:
            pred_chn_weight = pred_chn_weight.squeeze()
            loss = criterion(pred_chn_weight, tanh(target))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.update(loss.item())

        hidden = repackage_hidden(hidden)
        pred_chn_weight, hidden = modelw(inp, hidden)
        
    return losses.avg


def vis_features(st_loader, model, modelw, savefolder):
    global features_blobs
    model.eval()
    modelw.eval()
    feature_fusion = torch.ones(batch_size,512,1,1).to(device)
    pred_chn_weight = None
    downsample = nn.AvgPool2d(16)
    hidden = None
    for i, sample in enumerate(st_loader):
        if i<100:
            continue
        if i>1000:
            return
        currname = sample['imname'][-1][:-4]
        input_s = sample['image']
        target = sample['gt']
        input_t = sample['flow']
        input_s = input_s.float().to(device)
        input_t = input_t.float().to(device)
        target = target.float().to(device)
        input_var_s = input_s
        input_var_t = input_t
        target_var = target

        target_flat = downsample(target_var).view(target_var.size(0), target_var.size(1), -1)
        _, maxind = torch.max(target_flat, 2)
        feature_fusion = torch.ones(batch_size,512,1,1).to(device)
        features_blobs = []
        _ = model(input_var_s, input_var_t, feature_fusion, i)
        feature_fusion = features_blobs[0]

        cfeature = crop_feature_var(feature_fusion, maxind, 5)
        chn_weight = cfeature.view(cfeature.size(0), cfeature.size(1), -1)
        chn_weight = torch.mean(chn_weight, 2)

        weighted_feature = feature_fusion * chn_weight.view(batch_size, 512, 1, 1)
        weighted_feature = torch.sum(weighted_feature, 1)
        weighted_feature = weighted_feature[0,:,:].data.cpu().numpy()
        weighted_feature = weighted_feature - np.amin(weighted_feature)
        weighted_feature = weighted_feature / np.amax(weighted_feature)
        weighted_feature = np.uint8(255*weighted_feature)
        weighted_feature = cv2.resize(weighted_feature, (224,224))
        img = cv2.imread('../gtea_images/' + sample['imname'][0])
        heatmap = cv2.applyColorMap(weighted_feature, cv2.COLORMAP_JET)
        result = heatmap*0.3 + img*0.5
        cv2.imwrite(savefolder + 'gt_' + sample['imname'][0], result)

        weighted_feature = feature_fusion
        weighted_feature = torch.sum(weighted_feature, 1)
        weighted_feature = weighted_feature[0,:,:].data.cpu().numpy()
        weighted_feature = weighted_feature - np.amin(weighted_feature)
        weighted_feature = weighted_feature / np.amax(weighted_feature)
        weighted_feature = np.uint8(255*weighted_feature)
        weighted_feature = cv2.resize(weighted_feature, (224,224))
        img = cv2.imread('../gtea_images/' + sample['imname'][0])
        heatmap = cv2.applyColorMap(weighted_feature, cv2.COLORMAP_JET)
        result = heatmap*0.3 + img*0.5
        cv2.imwrite(savefolder + 'noweight_' + sample['imname'][0], result)

        if pred_chn_weight is not None:
            weighted_feature = feature_fusion * pred_chn_weight.view(batch_size, 512, 1, 1)
            weighted_feature = torch.sum(weighted_feature, 1)
            weighted_feature = weighted_feature[0,:,:].data.cpu().numpy()
            weighted_feature = weighted_feature - np.amin(weighted_feature)
            weighted_feature = weighted_feature / np.amax(weighted_feature)
            weighted_feature = np.uint8(255*weighted_feature)
            weighted_feature = cv2.resize(weighted_feature, (224,224))

            img = cv2.imread('../gtea_images/' + sample['imname'][0])
            heatmap = cv2.applyColorMap(weighted_feature, cv2.COLORMAP_JET)
            result = heatmap*0.3 + img*0.5
            cv2.imwrite(savefolder + 'pred_' + sample['imname'][0], result)
        gaze = target_var.data.cpu().numpy()
        gaze = gaze[0,:,:,:].squeeze()
        io.imsave(savefolder + 'gaze_' + sample['imname'][0], gaze)


        #print chn_weight.size()   #(batch_size,512)
        chn_weight = chn_weight.unsqueeze(1)  #(seq_len, batch, input_size)
        chn_weight = chn_weight.to(device)
        hidden = repackage_hidden(hidden)
        pred_chn_weight, hidden = modelw(chn_weight, hidden)  #pred size (seq_len, batch, output_size) ie (batch_size, 1, 512)
        pred_chn_weight = pred_chn_weight.squeeze()
        feature_fusion = (pred_chn_weight+1)/2  #turn to range(0,1)
        feature_fusion = feature_fusion.view(batch_size, 512, 1, 1)

def get_weighted(chn_weight, feature):
    #chn_weight (512), feature(1,512,14,14)
    chn_weight = chn_weight.view(1,512,1,1)
    feature = feature * chn_weight
    feature = torch.sum(feature, 1)
    feature = feature - torch.min(feature)
    feature = feature / torch.max(feature)
    #feature = feature - torch.mean(feature)
    return feature

def extract_late(epoch, st_loader, model, modelw, pred_folder=args.extract_late_pred_folder, feat_folder=args.extract_late_feat_folder):
    # pred is the gaze prediction result of SP, feat is the output of AT.
    if not os.path.exists(pred_folder):
        os.makedirs(pred_folder)
    if not os.path.exists(feat_folder):
        os.makedirs(feat_folder)
    global features_blobs
    losses = AverageMeter()
    auc = AverageMeter()
    aae = AverageMeter()
    aucm = AverageMeter()
    aaem = AverageMeter()
    auc2 = AverageMeter()
    aae2 = AverageMeter()
    model.eval()
    modelw.eval()
    hidden = None
    currname = None
    for i, sample in tqdm(enumerate(st_loader)):
        currname = sample['imname'][0]
        fixsac = sample['fixsac']
        input_s = sample['image']
        target = sample['gt']
        input_t = sample['flow']
        input_s = input_s.float().to(device)
        input_t = input_t.float().to(device)
        target = target.float().to(device)
        input_var_s = input_s
        input_var_t = input_t
        target_var = target #(1,1,224,224)
        features_blobs = []
        output = model(input_var_s, input_var_t)  #(1,1,224,224)
        feature_s = features_blobs[0]  #(1,512,14,14)

        outim = output.cpu().data.numpy().squeeze() #(224,224)
        targetim = target_var.cpu().data.numpy().squeeze() #(224,224)
        outim = np.uint8(255*outim)
        cv2.imwrite(os.path.join(pred_folder,currname), outim)

        aae1, auc1, pred_gp = computeAAEAUC(outim,targetim)
        #aucm.update(auc1)
        #aaem.update(aae1)

        cfeature = crop_feature(feature_s, pred_gp, 3) #(1,512,3,3)
        cfeature = cfeature.contiguous()
        chn_weight = cfeature.view(cfeature.size(0), cfeature.size(1), -1)
        chn_weight = torch.mean(chn_weight, 2)  #(1,512)
        if int(fixsac) == 1:
            feat = get_weighted(chn_weight, feature_s)
        else:
            hidden = repackage_hidden(hidden)
            chn_weight, hidden = modelw(chn_weight.unsqueeze(0), hidden)
            chn_weight = chn_weight.squeeze(0)
            feat = get_weighted(chn_weight, feature_s)
        feat = feat.cpu().data.numpy().squeeze()
        feat = np.uint8(255*feat)
        feat = cv2.resize(feat, (224,224))
        cv2.imwrite(os.path.join(feat_folder,currname), feat)


def train_late(epoch, loader, model, criterion, optimizer):
    losses = AverageMeter()
    auc = AverageMeter()
    aae = AverageMeter()
    for i,sample in enumerate(loader):
        im = sample['im']
        gt = sample['gt']
        feat = sample['feat']
        im = im.float().to(device)
        gt = gt.float().to(device)
        feat = feat.float().to(device)
        out = model(feat, im)
        loss = criterion(out, gt)
        outim = out.cpu().data.numpy().squeeze()
        targetim = gt.cpu().data.numpy().squeeze()
        aae1, auc1, _ = computeAAEAUC(outim,targetim)
        auc.update(auc1)
        aae.update(aae1)
        losses.update(loss.item())
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if (i+1)%300 == 0:
            print('Epoch: [{0}][{1}/{2}]\t''AUCAAE_late {auc.avg:.3f} ({aae.avg:.3f})\t''Loss {loss.val:.4f} ({loss.avg:.4f})\t'.format(
                epoch, i+1, len(loader)+1, auc = auc, loss= losses, aae=aae,))

    return losses.avg, auc.avg, aae.avg

def val_late(epoch, loader, model, criterion):
    losses = AverageMeter()
    auc = AverageMeter()
    aae = AverageMeter()
    with torch.no_grad():
        for i,sample in enumerate(loader):
            im = sample['im']
            gt = sample['gt']
            feat = sample['feat']
            im = im.float().to(device)
            gt = gt.float().to(device)
            feat = feat.float().to(device)
            out = model(feat, im)
            loss = criterion(out, gt)
            outim = out.cpu().data.numpy().squeeze()
            targetim = gt.cpu().data.numpy().squeeze()
            aae1, auc1, _ = computeAAEAUC(outim,targetim)
            auc.update(auc1)
            aae.update(aae1)
            losses.update(loss.item())
            if (i+1) % 1000 == 0:
                print('Epoch: [{0}][{1}/{2}]\t''AUCAAE_late {auc.avg:.3f} ({aae.avg:.3f})\t''Loss {loss.val:.4f} ({loss.avg:.4f})\t'.format(
                    epoch, i+1, len(loader)+1, auc = auc, loss= losses, aae=aae,))

    return losses.avg, auc.avg, aae.avg


if __name__ == '__main__':

    #trained_model = 'savelstm/3layerall/0net.pth.tar'
    print('building pretrained model ...')
    model = VGG_st_3dfuse(make_layers(cfg['D'], 3), make_layers(cfg['D'], 20))
    pretrained_dict = torch.load(args.pretrained_model)
    model_dict = model.state_dict()
    #model_dict.update(pretrained_dict)
    model_dict.update(pretrained_dict['state_dict'])
    model.load_state_dict(model_dict, strict=False)
    
    model.to(device)

    model._modules.get(hook_name).register_forward_hook(hook_feature)
    if args.loss_function != 'f':
        criterion = torch.nn.BCELoss().to(device)
    else:
        criterion = floss().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-7)

    modelw = lstmnet()
    modelw.to(device)
    criterionw = nn.MSELoss().to(device)
    optimizerw = torch.optim.Adam(modelw.parameters(), lr=1e-4)

    if not args.train_lstm: # then load pretrained lstm
        trained_model = args.pretrained_lstm #'../savelstm/best_train3_lstmnet.pth.tar'
        pretrained_dict = torch.load(trained_model)
        model_dict = modelw.state_dict()
        model_dict.update(pretrained_dict)
        modelw.load_state_dict(model_dict)

    load_late = False
    model_late = late_fusion()
    model_late.to(device)
    if not args.train_late:  # then load pretrained late fusion model
        trained_model = args.pretrained_late
        pretrained_dict = torch.load(trained_model)
        model_dict = model_late.state_dict()
        model_dict.update(pretrained_dict)
        model_late.load_state_dict(model_dict)
        del pretrained_dict

    optimizer_late = torch.optim.Adam(model_late.parameters(), lr=1e-4)
    criterion = floss().to(device)
    print('init done!')
    
    #vis_features(STTrainLoader, model, modelw, 'savelstm/3layerall/vistrainrelu/')


    from data.wdatas import wTrainData, wValData
    wTrainLoader = DataLoader(dataset=wTrainData, batch_size=1, shuffle=False, num_workers=0)
    wValLoader = DataLoader(dataset=wValData, batch_size=1, shuffle=False, num_workers=0)
    if args.train_lstm:
        print('begin training lstm....')
        #trainw(0, wTrainLoader, modelw, criterionw, optimizerw, val=True)
        #trainw(0, wTrainLoader, modelw, criterionw, optimizerw)
        prev = 999
        prevt = 999
        for epoch in range(120):
            #lr = raw_input('please input lr:')
            #lr = float(lr)
            #adjust_learning_rate(optimizerw, epoch, lr)
            l = trainw(epoch, wTrainLoader, modelw, criterionw, optimizerw, verbose = False)
            print ('---------train loss: %f-----------'%l)
            if l < prevt:
                torch.save(modelw.state_dict(), os.path.join(args.save_path, args.save_lstm))
            l = trainw(0, wValLoader, modelw, criterionw, optimizerw)
            print ('----------val loss: %f-------------'%l)
            if l<prev:
                prev=l
                torch.save(modelw.state_dict(), os.path.join(args.save_path, 'val'+args.save_lstm))
    print('lstm training finished!')

    if args.extract_late:
        extract_late(0, DataLoader(dataset=STValData, batch_size=1, shuffle=False, num_workers=1, pin_memory=True), model, modelw)
        extract_late(0, DataLoader(dataset=STTrainData, batch_size=1, shuffle=False, num_workers=1, pin_memory=True), model, modelw)

    #STTrainLoader = DataLoader(dataset=STTrainData, batch_size=1, shuffle=False, num_workers=1, pin_memory=True)
    #STValLoader = DataLoader(dataset=STValData, batch_size=1, shuffle=False, num_workers=1, pin_memory=True)

    del model, optimizer
    del modelw, optimizerw
    from data.lateDataset import lateDatasetTrain, lateDatasetVal
    train_loader = DataLoader(dataset = lateDatasetTrain, batch_size = 32, shuffle=False, num_workers=1, pin_memory=True)
    val_loader = DataLoader(dataset = lateDatasetVal, batch_size = 32, shuffle=False, num_workers=1, pin_memory=True)
    trainprev = 999
    valprev = 999
    loss_train = []
    loss_val = []
    for epoch in range(args.num_epoch):
        if args.train_late:
            print ('begin training model epoch %03d....'%epoch)
            loss, auc, aae = train_late(epoch, train_loader, model_late, criterion, optimizer_late)
            loss_train.append(loss)
            print('training, auc is %5f, aae is %5f'%(auc, aae))
            if loss < trainprev:
                torch.save({'state_dict': model_late.state_dict(), 'loss': loss, 'auc': auc, 'aae': aae}, os.path.join(args.save_path, args.save_late))
                trainprev = loss
        print('begin validation...')
        loss, auc, aae = val_late(epoch, val_loader, model_late, criterion)
        loss_val.append(loss)
        print('val, auc is %5f, aae is %5f'%(auc, aae))
        plot_loss(loss_train, loss_val, os.path.join(args.save_path, args.late_save_img))
        if loss < valprev:
            torch.save({'state_dict': model_late.state_dict(), 'loss': loss, 'auc': auc, 'aae': aae}, os.path.join(args.save_path, 'val'+args.save_late))
            valprev = loss
        if not args.train_late:
            break